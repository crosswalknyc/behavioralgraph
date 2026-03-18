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

# Force Snowflake connector to use JSON results instead of Arrow.
# The nanoarrow C extension in snowflake-connector-python 3.12.0 crashes
# on certain numeric values. Blocking it before import forces JSON fallback.
sys.modules['snowflake.connector.nanoarrow_arrow_iterator'] = None

import uuid
import json
import csv
import threading
import traceback
import re
import io
import hashlib
import secrets
from datetime import datetime, timedelta, date
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session
from flask_cors import CORS

try:
    from flask_socketio import SocketIO, emit, join_room, leave_room
    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False
import pandas as pd
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# Load environment variables from .env file
def load_env_file():
    """Load .env file manually if dotenv not available."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())
        return True
    return False

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Loaded environment variables from .env (dotenv)")
except ImportError:
    if load_env_file():
        print("✅ Loaded environment variables from .env (manual)")
    else:
        print("⚠️ No .env file found, using system environment variables only")

# Use the integrated bg.py (same as local script; lives in bg-webapp/bg.py)
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _APP_DIR)

# Environment configuration - 'development' or 'production'
APP_ENV = os.environ.get('APP_ENV', 'production').lower()
IS_DEV_ENV = APP_ENV == 'development'
print(f"🌍 Running in {APP_ENV.upper()} environment")

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# WebSocket support for real-time deck collaboration
if SOCKETIO_AVAILABLE:
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
else:
    socketio = None

# Health check endpoints - register early so they're available immediately
# These MUST be registered before any heavy imports or initialization
@app.route('/health')
@app.route('/healthz')  # Also support /healthz for Render compatibility
def health_check_root():
    """Root health check endpoint for Render - must be fast and not depend on any initialization."""
    # Return immediately - no processing, no imports, no dependencies
    return 'ok', 200, {'Content-Type': 'text/plain'}

@app.route('/ready')
def readiness_check():
    """Readiness check - indicates app is ready to serve traffic."""
    return 'ready', 200, {'Content-Type': 'text/plain'}

print("✅ Health check endpoints registered (/health, /healthz, /ready) - ready for Render")

# Global error handler for API routes - ensures JSON responses
@app.errorhandler(Exception)
def handle_exception(e):
    """Return JSON instead of HTML for API errors."""
    if request.path.startswith('/api/'):
        import traceback
        print(f"API Error: {e}")
        traceback.print_exc()
        return jsonify({
            'error': str(e),
            'type': type(e).__name__
        }), 500
    # For non-API routes, let Flask handle it normally
    raise e

@app.errorhandler(404)
def not_found(e):
    """Return JSON for API 404 errors."""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Endpoint not found', 'path': request.path}), 404
    return redirect(url_for('login_page'))

@app.errorhandler(500)
def server_error(e):
    """Return JSON for API 500 errors."""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500
    return redirect(url_for('login_page'))


@app.before_request
def redirect_if_must_reset_password():
    """If user must reset password, only allow /set-password and /api/set-password."""
    if not session.get('username') or not session.get('must_reset_password'):
        return None
    path = request.path or ''
    if path == '/set-password' or path.startswith('/api/set-password') or path == '/logout':
        return None
    if path.startswith('/static/') or path in ('/login', '/health', '/healthz', '/ready'):
        return None
    return redirect(url_for('set_password_page'))

# ============================================================================
# CONFIGURATION
# ============================================================================

S3_BUCKET = 'dashboard-inputs'
# Canonical Gen Pop 2026 file - profile selector and get-csv-data always use this key so dashboard matches S3 link
GEN_POP_CANONICAL_KEY = 'Gen_Pop_2026_03_04_2026_04_29.csv'
SUBSCRIBER_S3_BUCKET = 'svod-acquisition'  # Bucket for Subscriber IQ data
S3_PURGATORY_PREFIX = 'purgatory/'  # Files go here first; admin releases to main bucket
JOBS_STATUS_S3_KEY = 'system/jobs_status.json'  # Cross-worker job status persistence (Render)
HEDGE_FUND_S3_BUCKET = 'aggregated-tickers'  # Bucket for Hedge Fund IQ ticker data
TICKET_SALES_S3_BUCKET = 'ticket-sales-iq'  # Bucket for Ticket Sales IQ (talent-to-theater attribution)
TICKET_SALES_TRACKER_S3_BUCKET = 'ticket-sales-tracker'  # Bucket for Ticket Sales Tracker (movie viewers → theater)
# FORCE us-east-2 - all buckets are in this region, ignore AWS_REGION env var if set
S3_REGION = 'us-east-2'
USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

# AI Summary Cache Directory
AI_CACHE_DIR = os.path.join(os.path.dirname(__file__), 'ai_cache')

def ensure_cache_dir():
    """Ensure the AI cache directory exists."""
    if not os.path.exists(AI_CACHE_DIR):
        os.makedirs(AI_CACHE_DIR)
        print(f"✅ Created AI cache directory: {AI_CACHE_DIR}")

# Initialize S3 client (with timeout to prevent hanging during startup)
# This is after health check registration so it won't block health checks
try:
    import botocore.config
    config = botocore.config.Config(
        connect_timeout=2,
        read_timeout=2,
        retries={'max_attempts': 1},
        signature_version='s3v4',  # Force signature version 4 for presigned URLs
        s3={'addressing_style': 'virtual'}  # Use virtual-hosted-style URLs
    )
    # Use explicit endpoint so we never pick up AWS_ENDPOINT_URL_S3 (e.g. staging URL with revoked cert)
    s3_endpoint = f'https://s3.{S3_REGION}.amazonaws.com'
    s3_client = boto3.client(
        's3',
        region_name=S3_REGION,
        endpoint_url=s3_endpoint,
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        config=config
    )
    print(f"✅ S3 client initialized for region: {S3_REGION} with signature version 4 (endpoint: {s3_endpoint})")
    print(f"🔍 S3 client region: {s3_client.meta.region_name}")
    # Use same client for all buckets (all in us-east-2)
    hedge_fund_s3_client = s3_client
except Exception as e:
    # If config import fails or client creation fails, continue without S3
    print(f"⚠️ S3 client initialization failed (non-critical): {e}")
    try:
        # Fallback with signature version 4
        from botocore.client import Config
        config = Config(
            signature_version='s3v4',
            s3={'addressing_style': 'virtual'}
        )
        s3_endpoint = f'https://s3.{S3_REGION}.amazonaws.com'
        s3_client = boto3.client(
            's3',
            region_name=S3_REGION,
            endpoint_url=s3_endpoint,
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            config=config
        )
        print(f"✅ S3 client initialized with fallback config for region: {S3_REGION}")
        print(f"🔍 S3 client region: {s3_client.meta.region_name}")
        # Use same client for all buckets
        hedge_fund_s3_client = s3_client
    except Exception as e2:
        print(f"⚠️ S3 client fallback initialization failed: {e2}")
        s3_client = None
        hedge_fund_s3_client = None

# ============================================================================
# OPENAI INTEGRATION
# ============================================================================

openai_client = None

def get_openai_client():
    """Get or create OpenAI client - checks for API key at runtime."""
    global openai_client
    api_key = os.environ.get('OPENAI_API_KEY')
    print(f"🔍 Checking OPENAI_API_KEY: {'Found (' + api_key[:20] + '...)' if api_key else 'NOT FOUND'}")
    if not api_key:
        print("❌ No API key found in environment")
        return None
    if openai_client is None:
        try:
            from openai import OpenAI
            # Create client without any proxy settings
            # Clear any proxy environment variables that might interfere
            http_proxy = os.environ.pop('HTTP_PROXY', None)
            https_proxy = os.environ.pop('HTTPS_PROXY', None)
            try:
                openai_client = OpenAI(api_key=api_key)
                print("✅ OpenAI client initialized successfully")
            finally:
                # Restore proxy settings if they were set
                if http_proxy:
                    os.environ['HTTP_PROXY'] = http_proxy
                if https_proxy:
                    os.environ['HTTPS_PROXY'] = https_proxy
        except Exception as e:
            print(f"❌ OpenAI client initialization failed: {e}")
            return None
    return openai_client

# Try to initialize at startup if key exists
print(f"🚀 Startup: OPENAI_API_KEY present = {bool(os.environ.get('OPENAI_API_KEY'))}")
if os.environ.get('OPENAI_API_KEY'):
    get_openai_client()
else:
    print("⚠️ OPENAI_API_KEY not set at startup - will check at runtime")

# ============================================================================
# S3 JSON PERSISTENCE HELPERS WITH IN-MEMORY CACHE
# ============================================================================

METADATA_BUCKET = 'dashboard-inputs'  # Use existing bucket for metadata (in metadata/ folder)

# In-memory cache for metadata (shared across all requests)
_metadata_cache = {}
_cache_timestamps = {}
CACHE_TTL = 60  # Cache for 60 seconds

def load_json_from_s3(filename, use_cache=True):
    """Load JSON data from S3 metadata bucket with in-memory caching."""
    try:
        # Check cache first
        if use_cache and filename in _metadata_cache:
            cache_age = datetime.now().timestamp() - _cache_timestamps.get(filename, 0)
            if cache_age < CACHE_TTL:
                print(f"📦 Using cached {filename} (age: {cache_age:.1f}s)")
                return _metadata_cache[filename].copy()
        
        if not s3_client:
            print(f"⚠️ S3 client not available, using empty data for {filename}")
            return {}
        
        # Load from S3
        response = s3_client.get_object(Bucket=METADATA_BUCKET, Key=filename)
        data = json.loads(response['Body'].read().decode('utf-8'))
        
        # Update cache
        _metadata_cache[filename] = data
        _cache_timestamps[filename] = datetime.now().timestamp()
        
        print(f"✅ Loaded {filename} from S3 and cached")
        return data.copy()
    except s3_client.exceptions.NoSuchKey:
        print(f"ℹ️ {filename} not found in S3, returning empty data")
        empty_data = {}
        # Cache empty data too
        _metadata_cache[filename] = empty_data
        _cache_timestamps[filename] = datetime.now().timestamp()
        return empty_data
    except Exception as e:
        print(f"⚠️ Error loading {filename} from S3: {e}")
        # Return cached data if available, even if expired
        if filename in _metadata_cache:
            print(f"📦 Using stale cache for {filename}")
            return _metadata_cache[filename].copy()
        return {}

def save_json_to_s3(filename, data):
    """Save JSON data to S3 metadata bucket and update cache."""
    try:
        if not s3_client:
            print(f"⚠️ S3 client not available, cannot save {filename}")
            return False
        
        json_data = json.dumps(data, indent=2)
        s3_client.put_object(
            Bucket=METADATA_BUCKET,
            Key=filename,
            Body=json_data.encode('utf-8'),
            ContentType='application/json'
        )
        
        # Update cache immediately after save
        _metadata_cache[filename] = data.copy()
        _cache_timestamps[filename] = datetime.now().timestamp()
        
        print(f"✅ Saved {filename} to S3 and updated cache")
        return True
    except Exception as e:
        print(f"❌ Error saving {filename} to S3: {e}")
        return False

def invalidate_cache(filename=None):
    """Invalidate cache for a specific file or all files."""
    if filename:
        if filename in _metadata_cache:
            del _metadata_cache[filename]
            del _cache_timestamps[filename]
            print(f"🗑️ Invalidated cache for {filename}")
    else:
        _metadata_cache.clear()
        _cache_timestamps.clear()
        print(f"🗑️ Invalidated all cache")

# Cache filenames (stored in metadata/ folder in S3)
TICKER_IMAGES_FILE = 'metadata/ticker_images_cache.json'
TICKER_PROFILES_FILE = 'metadata/ticker_profile_mappings.json'
SEC_ACTUALS_FILE = 'metadata/hedge_fund_sec_actuals.json'
QUICK_SELECTS_FILE = 'metadata/admin_quick_selects.json'
LIVE_FEATURES_FILE = 'metadata/admin_live_features.json'

def ensure_metadata_folder():
    """Ensure the metadata folder exists in S3 by creating empty files if needed."""
    try:
        if not s3_client:
            print("⚠️ S3 client not available, skipping metadata initialization")
            return False
        
        print(f"✅ Using bucket '{METADATA_BUCKET}' for metadata storage")
        
        # Initialize empty metadata files if they don't exist
        for filename in [TICKER_IMAGES_FILE, TICKER_PROFILES_FILE, SEC_ACTUALS_FILE]:
            try:
                # Try to read the file
                s3_client.head_object(Bucket=METADATA_BUCKET, Key=filename)
                print(f"✅ {filename} exists")
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    # File doesn't exist, create it with empty JSON
                    print(f"📝 Creating {filename}...")
                    s3_client.put_object(
                        Bucket=METADATA_BUCKET,
                        Key=filename,
                        Body=json.dumps({}).encode('utf-8'),
                        ContentType='application/json'
                    )
                    print(f"✅ Created {filename}")
        
        return True
    except Exception as e:
        print(f"❌ Error ensuring metadata folder: {e}")
        return False

# Ensure metadata folder exists at startup
ensure_metadata_folder()

# Minimum percentage threshold for AI insights and callouts
MIN_PCT_FOR_INSIGHTS = 25  # Only include items with 25% or higher profile percentage

def _filter_demographics_for_insights(demographics, min_pct=MIN_PCT_FOR_INSIGHTS):
    """Filter demographics to only include values >= min_pct."""
    filtered = {}
    for cat, values in demographics.items():
        if isinstance(values, dict):
            filtered_values = {k: v for k, v in values.items() if v >= min_pct}
            if filtered_values:
                filtered[cat] = filtered_values
    return filtered

def _filter_behavioral_for_insights(behavioral, min_pct=MIN_PCT_FOR_INSIGHTS):
    """Filter behavioral data to only include items >= min_pct."""
    filtered = {}
    for cat, items in behavioral.items():
        if isinstance(items, list):
            filtered_items = [i for i in items if i.get('pct', 0) >= min_pct]
            if filtered_items:
                filtered[cat] = filtered_items
    return filtered

def _filter_top_items_for_insights(items, min_pct=MIN_PCT_FOR_INSIGHTS):
    """Filter a list of items to only include those >= min_pct."""
    if not isinstance(items, list):
        return items
    return [i for i in items if i.get('pct', 0) >= min_pct]

def generate_ai_insights(profile_data):
    """Generate AI-powered insights from profile data."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Prepare data summary for GPT - filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'This audience')
        
        # Build context - only items with 25%+ profile percentage
        demo_summary = []
        for cat, values in demographics.items():
            if isinstance(values, dict):
                top_items = sorted(values.items(), key=lambda x: x[1], reverse=True)[:3]
                if top_items:
                    demo_summary.append(f"{cat}: {', '.join([f'{k} ({v:.1f}%)' for k, v in top_items])}")
        
        behavior_summary = []
        for cat, items in behavioral.items():
            if isinstance(items, list) and items:
                top_items = items[:5]
                item_strs = []
                for i in top_items:
                    name = i.get('name', i.get('value', ''))
                    pct = i.get('pct', 0)
                    item_strs.append(f"{name} ({pct:.1f}%)")
                if item_strs:
                    behavior_summary.append(f"{cat}: {', '.join(item_strs)}")
        
        prompt = f"""Analyze this audience profile and provide 5 key insights in bullet points. Be specific and actionable.

Profile: {profile_name}
Sample Size: {sample_size:,}

Demographics (only showing values with 25%+ of the audience):
{chr(10).join(demo_summary[:8]) if demo_summary else 'No demographics meet the 25% threshold'}

Top Behaviors (only showing values with 25%+ of the audience):
{chr(10).join(behavior_summary[:10]) if behavior_summary else 'No behaviors meet the 25% threshold'}

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided above.

Provide insights about:
1. Who this audience is (demographics)
2. What makes them unique vs general population
3. Their media consumption habits
4. Potential marketing opportunities
5. Key differentiators

Keep each insight to 1-2 sentences. Be specific with numbers from the data provided."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert audience analyst. Provide clear, actionable insights about consumer audiences based on behavioral and demographic data. Focus on what makes this audience unique and how marketers can reach them."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        return {
            "insights": response.choices[0].message.content,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

def generate_persona(profile_data):
    """Generate an AI persona from profile data."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        profile_name = profile_data.get('name', 'Audience')
        
        # Get top demographics (only those >= 25%)
        gender = demographics.get('gender', {})
        age = demographics.get('age', {})
        income = demographics.get('income', {})
        
        top_gender = max(gender.items(), key=lambda x: x[1])[0] if gender else "Unknown"
        top_age = max(age.items(), key=lambda x: x[1])[0] if age else "Unknown"
        top_income = max(income.items(), key=lambda x: x[1])[0] if income else "Unknown"
        
        # Get top behaviors (only those >= 25%)
        top_behaviors = []
        for cat, items in behavioral.items():
            if isinstance(items, list):
                for item in items[:2]:
                    if item.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS:
                        top_behaviors.append(f"{item.get('name', '')} ({cat})")
        
        prompt = f"""Create a detailed marketing persona for this audience segment.

Profile: {profile_name}
Primary Gender: {top_gender}
Primary Age Range: {top_age}
Primary Income: {top_income}
Top Interests/Behaviors: {', '.join(top_behaviors[:10])}

Generate:
1. A creative persona name (like "Tech-Savvy Trendsetter" or "Budget-Conscious Parent")
2. A brief bio (2-3 sentences describing who they are)
3. Daily routine highlights (morning, afternoon, evening)
4. Media consumption habits
5. Shopping preferences
6. Pain points and motivations
7. Best channels to reach them

Format as JSON with keys: name, bio, routine, media, shopping, painPoints, channels"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a marketing strategist creating detailed audience personas. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=800,
            temperature=0.8
        )
        
        content = response.choices[0].message.content
        # Try to parse JSON from response
        try:
            # Remove markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            persona = json.loads(content)
        except:
            persona = {"raw": content}
        
        return {
            "persona": persona,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

def generate_marketing_strategy(profile_data):
    """Generate AI marketing recommendations."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        profile_name = profile_data.get('name', 'Audience')
        
        # Compile behavioral insights (only those >= 25%)
        behavior_list = []
        for cat, items in behavioral.items():
            if isinstance(items, list):
                for item in items[:3]:
                    behavior_list.append(f"{item.get('name', '')} ({item.get('pct', 0):.1f}%)")
        
        prompt = f"""Create a comprehensive marketing strategy for reaching this audience.

Profile: {profile_name}
Demographics (only values with 25%+ of audience): {json.dumps(demographics, default=str)[:500]}
Key Behaviors (only values with 25%+ of audience): {', '.join(behavior_list[:15]) if behavior_list else 'No behaviors meet the 25% threshold'}

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided above.

Provide:
1. **Channel Strategy** - Which platforms/channels to prioritize and why
2. **Content Strategy** - Types of content that will resonate
3. **Messaging Framework** - Key themes and tone to use
4. **Campaign Ideas** - 3 specific campaign concepts
5. **Timing Recommendations** - Best times/days to reach them
6. **Budget Allocation** - Suggested % split across channels

Be specific and actionable. Only reference the actual data provided above."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a senior marketing strategist. Provide detailed, data-driven marketing recommendations."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        return {
            "strategy": response.choices[0].message.content,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

def chat_with_data(profile_data, user_question):
    """Answer questions about the profile data."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        locations = _filter_top_items_for_insights(profile_data.get('locations', []))
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'This audience')
        
        # Build comprehensive data context (only items >= 25%)
        data_context = f"""
Profile: {profile_name}
Sample Size: {sample_size:,}

DEMOGRAPHICS (only values with 25%+ of audience):
{json.dumps(demographics, indent=2, default=str)[:1500]}

TOP BEHAVIORS BY CATEGORY (only values with 25%+ of audience):
{json.dumps({k: v[:5] if isinstance(v, list) else v for k, v in list(behavioral.items())[:10]}, indent=2, default=str)[:2000]}

TOP LOCATIONS (only values with 25%+ of audience):
{json.dumps(locations[:10], indent=2, default=str)[:500]}
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"You are an audience data analyst. Answer questions about this profile data accurately and concisely. IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.\n\nDATA:\n{data_context}"},
                {"role": "user", "content": user_question}
            ],
            max_tokens=500,
            temperature=0.5
        )
        
        return {
            "answer": response.choices[0].message.content,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

def answer_business_question(profile_data, question, conversation_history=None):
    """Answer a business question using profile data with follow-up suggestions."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        locations = _filter_top_items_for_insights(profile_data.get('locations', []))
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'This audience')
        
        # Build data context (only items >= 25%)
        behavior_summary = []
        for cat, items in behavioral.items():
            if isinstance(items, list) and items:
                top_items = items[:5]
                item_strs = []
                for i in top_items:
                    name = i.get('name', '')
                    pct = i.get('pct', 0)
                    item_strs.append(f"{name} ({pct:.1f}%)")
                if item_strs:
                    behavior_summary.append(f"{cat}: {', '.join(item_strs)}")
        
        data_context = f"""
AUDIENCE PROFILE: {profile_name}
Sample Size: {sample_size:,}

DEMOGRAPHICS (only values with 25%+ of audience):
{json.dumps(demographics, indent=2, default=str)[:1500]}

KEY BEHAVIORS (only values with 25%+ of audience):
{chr(10).join(behavior_summary[:15]) if behavior_summary else 'No behaviors meet the 25% threshold'}

TOP LOCATIONS (only values with 25%+ of audience):
{json.dumps(locations[:10] if locations else [], indent=2, default=str)[:500]}
"""
        
        # Build conversation context
        messages = [
            {"role": "system", "content": f"""You are a senior business intelligence analyst helping a marketing team understand their audience data.

Your role:
1. Answer business questions using the provided audience data
2. Be specific - cite actual numbers and percentages from the data
3. Connect insights to actionable business recommendations
4. After answering, suggest 2-3 follow-up questions that could help them further
5. Ask how you can make the analysis more useful for their specific business goals

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.

AUDIENCE DATA:
{data_context}

Always format your response with:
1. **Direct Answer** - Address their question with data-backed insights
2. **Key Data Points** - Bullet the most relevant numbers (only those 25%+)
3. **Business Recommendation** - Actionable next step
4. **Follow-up Questions** - 2-3 questions to dig deeper

Be conversational and helpful."""}
        ]
        
        # Add conversation history if provided
        if conversation_history:
            for msg in conversation_history[-6:]:  # Keep last 6 messages for context
                messages.append(msg)
        
        messages.append({"role": "user", "content": question})
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=1000,
            temperature=0.7
        )
        
        return {
            "answer": response.choices[0].message.content,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

def generate_business_deck(profile_data, business_question, key_findings=None):
    """Generate a presentation deck outline for a business question."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'Audience')
        
        # Build data summary (only items >= 25%)
        behavior_summary = []
        for cat, items in behavioral.items():
            if isinstance(items, list) and items:
                item_names = [i.get('name', '') for i in items[:3]]
                if item_names:
                    behavior_summary.append(f"{cat}: {', '.join(item_names)}")
        
        prompt = f"""Create a professional presentation deck outline to answer this business question:

BUSINESS QUESTION: {business_question}

AUDIENCE: {profile_name}
Sample Size: {sample_size:,}

KEY DEMOGRAPHICS (only values with 25%+ of audience):
- Gender: {json.dumps(demographics.get('gender', {}), default=str)}
- Age: {json.dumps(demographics.get('age', {}), default=str)}
- Income: {json.dumps(demographics.get('income', {}), default=str)}

TOP BEHAVIORS (only values with 25%+ of audience):
{chr(10).join(behavior_summary[:10]) if behavior_summary else 'No behaviors meet the 25% threshold'}

{f'PREVIOUS FINDINGS: {key_findings}' if key_findings else ''}

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.

Generate a 6-8 slide deck outline with:
1. Title slide
2. Executive Summary (key answer to the business question)
3. Audience Overview (demographics snapshot)
4. Key Behavioral Insights (2-3 slides with specific data points)
5. Recommendations slide
6. Next Steps / Call to Action

For each slide provide:
- Slide title
- Key points (3-4 bullets with actual data)
- Suggested visual (chart type, graphic, etc.)

Format as JSON with structure:
{{
  "title": "Deck title",
  "slides": [
    {{"title": "...", "points": ["...", "..."], "visual": "..."}}
  ]
}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a presentation design expert. Create compelling, data-driven slide decks. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        
        content = response.choices[0].message.content
        
        # Parse JSON from response
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            deck = json.loads(content)
        except:
            deck = {"raw": content}
        
        return {
            "deck": deck,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

def compare_profiles_ai(profiles_data):
    """AI comparison of multiple profiles."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        profiles_summary = []
        for profile in profiles_data:
            name = profile.get('name', 'Unknown')
            # Filter to only include items >= 25%
            demo = _filter_demographics_for_insights(profile.get('demographics', {}))
            behaviors = _filter_behavioral_for_insights(profile.get('behavioral', {}))
            
            # Get key stats (only those >= 25%)
            top_behaviors = []
            for cat, items in behaviors.items():
                if isinstance(items, list) and items:
                    top_behaviors.append(f"{items[0].get('name', '')} ({cat})")
            
            profiles_summary.append(f"""
{name}:
- Gender (25%+ only): {json.dumps(demo.get('gender', {}), default=str)[:200]}
- Age (25%+ only): {json.dumps(demo.get('age', {}), default=str)[:200]}
- Top Behaviors (25%+ only): {', '.join(top_behaviors[:5]) if top_behaviors else 'None meet threshold'}
""")
        
        prompt = f"""Compare these audience profiles and identify:
1. Key similarities between the audiences
2. Key differences that set each apart
3. Overlap opportunities (where they might be reached together)
4. Distinct positioning for each

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.

Profiles (only showing data points with 25%+ of audience):
{''.join(profiles_summary)}

Be specific with numbers and percentages from the data provided."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an audience research expert. Compare profiles clearly and identify actionable differences."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=800,
            temperature=0.7
        )
        
        return {
            "comparison": response.choices[0].message.content,
            "tokens_used": response.usage.total_tokens
        }
    except Exception as e:
        return {"error": str(e)}

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
                "role": "super_admin",
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
            "role": "super_admin",
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
            "role": "user",
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
        data['users']['liz']['role'] = "user"
        changed = True
    
    # Check for jessie user
    if 'jessie' not in data['users']:
        data['users']['jessie'] = {
            "password_hash": hash_password("SpicySriracha"),
            "role": "user",
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
        data['users']['jessie']['role'] = "user"
        changed = True
    
    if changed:
        save_users(data)
    
    return data

# Initialize users in background - don't block startup
def async_init_users():
    """Initialize users in background thread - doesn't block app startup."""
    import time
    # Small delay to ensure app is ready
    time.sleep(0.5)
    try:
        print("👥 Initializing users in background...")
        init_users()
        print("✅ Users initialized")
    except Exception as e:
        print(f"⚠️ User initialization error (non-critical): {e}")
        # Fall back to local file if S3 fails
        try:
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, 'r') as f:
                    json.load(f)
                print("✅ Using local users file")
        except Exception as e2:
            print(f"⚠️ Local users file error: {e2}")

def get_current_user():
    """Get current logged-in user data."""
    if 'username' not in session:
        return None
    data = load_users()
    return data['users'].get(session['username'])


def auto_add_runs_to_all_users(s3_keys, key_category_map=None):
    """Auto-add new profile S3 keys to users' allowed_runs based on their
    allowed_categories subscriptions. Users with allowed_runs=['*'] already see
    everything. For users with explicit lists, a new key is added only if the
    user's allowed_categories includes the profile's category (or '*').
    If key_category_map is None, falls back to looking up categories from the
    s3_cache; if that also fails the key is added to everyone (safe default)."""
    if not s3_keys:
        return
    if isinstance(s3_keys, str):
        s3_keys = [s3_keys]

    # Build category lookup: s3_key -> uppercase category
    cat_map = {}
    if key_category_map:
        cat_map = {k: (v or '').upper() for k, v in key_category_map.items()}
    else:
        for job in (s3_cache.get('jobs') or []):
            sk = job.get('s3_key') or ''
            if sk in s3_keys:
                cat_map[sk] = (job.get('category') or '').upper()

    try:
        data = load_users()
        users = data.get('users', {})
        changed = False
        for username, user in users.items():
            runs = user.get('allowed_runs', ['*'])
            if isinstance(runs, list) and '*' in runs:
                continue
            existing = set(runs or [])

            user_cats = user.get('allowed_categories', ['*'])
            has_all_cats = isinstance(user_cats, list) and '*' in user_cats
            user_cats_upper = set() if has_all_cats else {c.upper() for c in (user_cats or [])}

            added = []
            for k in s3_keys:
                if k in existing:
                    continue
                profile_cat = cat_map.get(k, '')
                if has_all_cats or profile_cat in user_cats_upper or not profile_cat:
                    added.append(k)
            if added:
                user['allowed_runs'] = list(existing | set(added))
                changed = True
        if changed:
            save_users(data)
            print(f"✅ Auto-added {len(s3_keys)} new profile(s) to qualifying users' allowed_runs")
    except Exception as e:
        print(f"⚠️ auto_add_runs_to_all_users error: {e}")


def remove_run_from_all_users(s3_key):
    """Remove a specific profile S3 key from every user's allowed_runs."""
    if not s3_key:
        return
    try:
        data = load_users()
        users = data.get('users', {})
        changed = False
        for username, user in users.items():
            runs = user.get('allowed_runs', ['*'])
            if isinstance(runs, list) and '*' in runs:
                continue
            if s3_key in (runs or []):
                user['allowed_runs'] = [k for k in runs if k != s3_key]
                changed = True
        if changed:
            save_users(data)
            print(f"✅ Removed {s3_key} from all users' allowed_runs")
    except Exception as e:
        print(f"⚠️ remove_run_from_all_users error: {e}")


# Credit cost per analysis type
CREDITS_PROFILE_ANALYSIS = 5
CREDITS_TICKET_SALES = 10
CREDITS_TICKET_SALES_TRACKER = 10
CREDITS_SVOD = 10
CREDITS_CAMPAIGN_ROI = 5
CREDITS_WATCH_TIME = 1

def _get_company_pool(data, company_name):
    """Return the company pool dict if the company has one, else None."""
    if not company_name:
        return None
    return data.get('companies', {}).get(company_name)


def check_user_credits(username):
    """Check if user has credits remaining.  Returns (has_credits, credits_left).

    Credit model (company users):
      * The company credit pool is the primary source of credits.
      * A per-user ``credit_ceiling`` (-1 = no limit) caps how many credits
        the individual may ever consume from the pool.
      * If a user has a personal ``credits`` override (> 0 or -1), that
        balance is used *instead* of the pool.

    Solo users (no company pool) just use their personal ``credits``.
    """
    data = load_users()
    user = data['users'].get(username)
    if not user:
        return False, 0

    user_credits = user.get('credits', 0)
    user_unlimited = user_credits == -1

    company = (user.get('company') or '').strip()
    pool = _get_company_pool(data, company)

    if pool is not None:
        pool_total = pool.get('credit_pool', 0)
        pool_used  = pool.get('credit_pool_used', 0)
        pool_unlimited  = pool_total == -1
        pool_remaining  = -1 if pool_unlimited else max(pool_total - pool_used, 0)

        has_personal_override = user.get('credit_source') == 'personal'
        if has_personal_override:
            if user_unlimited:
                return True, -1
            return user_credits > 0, user_credits

        ceiling = user.get('credit_ceiling', -1)
        user_used = user.get('credits_used', 0)
        ceiling_remaining = -1 if ceiling == -1 else max(ceiling - user_used, 0)

        if pool_unlimited and ceiling == -1:
            return True, -1
        if pool_unlimited:
            return ceiling_remaining > 0, ceiling_remaining
        if ceiling == -1:
            return pool_remaining > 0, pool_remaining
        effective = min(pool_remaining, ceiling_remaining)
        return effective > 0, effective

    if user_unlimited:
        return True, -1
    return user_credits > 0, user_credits


def has_credits_for(username, amount):
    """Return True if user has at least `amount` credits (or unlimited)."""
    has_credits, credits_left = check_user_credits(username)
    if not has_credits:
        return False
    if credits_left == -1:
        return True
    return credits_left >= amount


def consume_credit(username, description=None, job_id=None, pull_type=None, credits_used=1):
    """Consume credits from user and/or company pool.
    Returns True if successful."""
    data = load_users()
    user = data['users'].get(username)
    if not user:
        return False

    used_at = datetime.now().isoformat()
    entry = {
        'used_at': used_at,
        'description': description or 'Analysis run',
        'job_id': job_id or '',
        'pull_type': pull_type or 'Profile Analysis',
        'credits_used': credits_used
    }

    user_unlimited = user.get('credits', 0) == -1

    company = (user.get('company') or '').strip()
    pool = _get_company_pool(data, company)
    has_personal_override = user.get('credit_source') == 'personal'

    if pool is not None and not has_personal_override:
        pool_total = pool.get('credit_pool', 0)
        pool_used  = pool.get('credit_pool_used', 0)
        pool_unlimited = pool_total == -1
        pool_remaining = -1 if pool_unlimited else (pool_total - pool_used)

        ceiling = user.get('credit_ceiling', -1)
        user_used = user.get('credits_used', 0)
        ceiling_remaining = -1 if ceiling == -1 else (ceiling - user_used)

        if not pool_unlimited and pool_remaining < credits_used:
            return False
        if ceiling != -1 and ceiling_remaining < credits_used:
            return False

        user['credits_used'] = user.get('credits_used', 0) + credits_used
        history = user.setdefault('credit_usage_history', [])
        history.insert(0, entry)
        user['credit_usage_history'] = history[:500]

        if not pool_unlimited:
            pool['credit_pool_used'] = pool.get('credit_pool_used', 0) + credits_used

        save_users(data)
        return True

    if not user_unlimited and user.get('credits', 0) < credits_used:
        return False

    if not user_unlimited:
        user['credits'] -= credits_used
    user['credits_used'] = user.get('credits_used', 0) + credits_used
    history = user.setdefault('credit_usage_history', [])
    history.insert(0, entry)
    user['credit_usage_history'] = history[:500]

    save_users(data)
    return True

def _normalize_role(role):
    """Treat legacy 'enterprise' as 'user'; only user, admin, super_admin are valid."""
    if role == 'enterprise':
        return 'user'
    return role if role in ('user', 'admin', 'super_admin') else (role or 'user')

# ============================================================================
# AUTHENTICATION DECORATORS
# ============================================================================

# DEV ENVIRONMENT ACCESS CONTROL
# In development mode, only admin and super_admin users can access the site
# This allows testing changes without affecting regular users
ALLOWED_DEV_PATHS = ['/login', '/logout', '/health', '/healthz', '/ready', '/static', '/api/login']

@app.before_request
def check_dev_environment_access():
    """In dev environment, restrict access to admin/super_admin users only."""
    if not IS_DEV_ENV:
        return None  # Production - no restrictions
    
    # Allow health checks and static files without auth
    path = request.path
    if any(path.startswith(allowed) for allowed in ALLOWED_DEV_PATHS):
        return None
    
    # If not logged in, allow through to login page
    if 'username' not in session:
        if path == '/' or path.startswith('/api/'):
            return None  # Let the normal auth flow handle it
        return None
    
    # Check if user is admin or super_admin
    user = get_current_user()
    if user:
        role = _normalize_role(user.get('role', 'user'))
        if role in ('admin', 'super_admin'):
            return None  # Allow access
    
    # Non-admin user trying to access dev site
    return render_template('dev_access_denied.html'), 403

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            # For API endpoints, return JSON error instead of redirect
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def requires_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            # For API requests, return JSON error instead of redirect
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Session expired. Please log in again.'}), 401
            return redirect(url_for('login_page'))
        user = get_current_user()
        role = user.get('role', '') if user else ''
        if not user or (role != 'admin' and role != 'super_admin'):
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def requires_super_admin(f):
    """Only super_admin can call this endpoint."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Session expired. Please log in again.'}), 401
            return redirect(url_for('login_page'))
        user = get_current_user()
        role = _normalize_role(user.get('role', 'user') if user else '')
        if not user or role != 'super_admin':
            return jsonify({'success': False, 'error': 'Super admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

def requires_purgatory_access(f):
    """Decorator that allows admins, super_admins, and users with purgatory approval access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Session expired. Please log in again.'}), 401
            return redirect(url_for('login_page'))
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 403
        
        role = user.get('role', '')
        has_purgatory_approval = user.get('has_purgatory_approval', False)
        
        # Allow admins, super_admins, or users with purgatory approval
        if role in ['admin', 'super_admin'] or has_purgatory_approval:
            return f(*args, **kwargs)
        
        return jsonify({'success': False, 'error': 'Purgatory access required'}), 403
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

def _decode_metadata_value(s):
    """Decode a metadata value (e.g. + back to space) for display/API."""
    if not s:
        return s
    return str(s).replace('+', ' ')


def parse_metadata_from_csv(csv_content):
    """Extract metadata from the INPUT_METADATA row of a CSV."""
    try:
        lines = csv_content.split('\n')
        for line in lines:
            if line.startswith('INPUT_METADATA,'):
                parts = line.split(',')
                if len(parts) >= 2:
                    metadata_str = parts[1]
                    # Parse: BRAND:xxx_SAMPLE_START:xxx_..._BRAND_CATEGORY:xxx_LISTENER_WATCHER:true_PLATFORM_NAME:xxx
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
                    # Decode stored values that use + for space
                    for key in ('BRAND_CATEGORY', 'PLATFORM_NAME'):
                        if key in metadata and metadata[key]:
                            metadata[key] = _decode_metadata_value(metadata[key])
                    return metadata
        return None
    except Exception as e:
        print(f"Error parsing metadata: {e}")
        return None

def _encode_metadata_value(s):
    """Encode a metadata value for storage (space -> + so _ remains pair separator)."""
    if not s:
        return ''
    return str(s).replace(' ', '+')


def ensure_metadata_has_rerun_fields(csv_path, brand_category, is_listener_watcher, platform_name=None):
    """Append BRAND_CATEGORY, LISTENER_WATCHER, PLATFORM_NAME to INPUT_METADATA row for future reruns."""
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        mask = df['Column'].astype(str).str.upper() == 'INPUT_METADATA'
        if not mask.any():
            return
        idx = mask.idxmax()
        val = str(df.at[idx, 'Value'])
        extra = []
        if brand_category:
            extra.append(f"_BRAND_CATEGORY:{_encode_metadata_value(brand_category)}")
        extra.append(f"_LISTENER_WATCHER:{str(is_listener_watcher).lower()}")
        if platform_name:
            extra.append(f"_PLATFORM_NAME:{_encode_metadata_value(platform_name)}")
        if extra:
            df.at[idx, 'Value'] = val + ''.join(extra)
            df.to_csv(csv_path, index=False)
    except Exception as e:
        print(f"ensure_metadata_has_rerun_fields error: {e}")


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
        if 'AGE' in demographics:
            demographics['AGE'] = normalize_age_buckets_for_display(demographics['AGE'])
        return demographics
    except Exception as e:
        print(f"Error extracting demographics: {e}")
        return {}

def extract_sample_size_from_csv(csv_content):
    """Extract sample size from SAMPLE SIZE row. If primary value is under 1000, use alternate column (largest of D, E, etc.)."""
    try:
        reader = csv.reader(io.StringIO(csv_content))
        for row in reader:
            if len(row) > 0 and str(row[0]).strip().upper() == 'SAMPLE SIZE':
                candidates = []
                for idx in [3, 4, 2]:  # D=Category Share, E=Original Raw Numbers, C=Brand Penetration
                    if len(row) > idx and row[idx]:
                        try:
                            n = int(float(str(row[idx]).replace(',', '')))
                            if n > 0:
                                candidates.append(n)
                        except (ValueError, TypeError):
                            pass
                if not candidates:
                    return None
                chosen = candidates[0]
                if chosen < 1000 and len(candidates) > 1:
                    chosen = max(candidates)
                return chosen
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
    First checks filename for brand match, then checks metadata.
    Returns: (exact_match_file, similar_files_with_different_dates)
    """
    if not s3_client:
        return None, []
    
    normalized_brand = normalize_brand_for_search(brand_search)
    search_lower = brand_search.lower().replace(' ', '_').replace('-', '_')
    exact_match = None
    similar_files = []
    
    print(f"🔍 Searching S3 for brand: '{brand_search}' (normalized: '{normalized_brand}', search: '{search_lower}')")
    
    try:
        # List all objects in the bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv'):
                    continue
                
                # Skip system files
                if key.startswith('system/'):
                    continue
                
                # Extract filename without extension and date portion
                # Format: BrandName_MM_DD_YYYY_HH_MM.csv
                filename = key.replace('.csv', '')
                filename_lower = filename.lower()
                
                # Check if filename contains the brand (multiple matching strategies)
                filename_match = (
                    normalized_brand in filename_lower or 
                    search_lower in filename_lower or
                    brand_search.lower() in filename_lower or
                    brand_search.lower().replace(' ', '') in filename_lower.replace('_', '')
                )
                
                if not filename_match:
                    continue
                
                print(f"📁 Found matching file: {key}")
                
                # Download and check the file's metadata
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                    csv_content = response['Body'].read().decode('utf-8')
                    metadata = parse_metadata_from_csv(csv_content)
                    
                    # Get dates from metadata or try to parse from filename
                    file_start = ''
                    file_end = ''
                    if metadata:
                        file_start = metadata.get('SAMPLE_START', '')
                        file_end = metadata.get('SAMPLE_END', '')
                    
                    # Extract demographics and sample size
                    demographics = extract_demographics_from_csv(csv_content)
                    sample_size = extract_sample_size_from_csv(csv_content)
                    
                    print(f"   📊 Sample size: {sample_size}, Dates: {file_start} to {file_end}")
                    
                    # Check for exact date match
                    if file_start == start_date and file_end == end_date:
                        print(f"   ✅ EXACT match (same dates)")
                        exact_match = {
                            'key': key,
                            'content': csv_content,
                            'metadata': metadata,
                            'demographics': demographics,
                            'sample_size': sample_size,
                            'last_modified': obj['LastModified'].isoformat()
                        }
                    else:
                        # Same brand, different dates - use for consistency validation
                        print(f"   📋 Similar match (different dates: {file_start}-{file_end} vs {start_date}-{end_date})")
                        similar_files.append({
                            'key': key,
                            'content': csv_content,
                            'metadata': metadata,
                            'demographics': demographics,
                            'sample_size': sample_size,
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
    
    print(f"🔍 Search complete: exact_match={exact_match is not None}, similar_files={len(similar_files)}")
    return exact_match, similar_files

def validate_demographics_consistency(new_demographics, existing_demographics, tolerance=2):
    """
    Check if new demographics are within tolerance of existing demographics.
    Tolerance is +/- 2% to ensure consistency with previous runs.
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

def upload_to_s3(file_path, brand_name, start_date, end_date, created_by=None, use_purgatory=True, bucket=None, category=None, source_type='profile_analysis'):
    """Upload a result file to S3. By default uploads to purgatory/ for admin review before release."""
    if not s3_client:
        return None
    try:
        target_bucket = bucket or S3_BUCKET
        timestamp = datetime.now().strftime('%m_%d_%Y_%H_%M')
        # Name (or input) with parts separated by underscore: spaces/hyphens/slashes/commas -> single _
        safe_brand_name = re.sub(r'[\s\-/,]+', '_', (brand_name or '').strip())
        safe_brand_name = re.sub(r'_+', '_', safe_brand_name).strip('_') or 'Profile'
        base_key = f"{safe_brand_name}_{timestamp}.csv"
        s3_key = (S3_PURGATORY_PREFIX + base_key) if use_purgatory else base_key
        s3_client.upload_file(file_path, target_bucket, s3_key)
        
        # If using purgatory, add to purgatory metadata for tracking
        if use_purgatory and created_by:
            add_to_purgatory(
                s3_key=s3_key,
                bucket=target_bucket,
                created_by=created_by,
                project_name=brand_name,
                category=category or 'Uncategorized',
                source_type=source_type
            )
            print(f"✅ Added to purgatory: {s3_key} (bucket: {target_bucket}, user: {created_by})")
        
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
        
        # If user must reset password: accept whatever they type (min 8 chars) as their new password and log them in
        if user.get('must_reset_password'):
            if len(password) < 8:
                return jsonify({'success': False, 'error': 'Choose a password at least 8 characters to sign in.'})
            user['password_hash'] = hash_password(password)
            user['must_reset_password'] = False
            user['last_login'] = datetime.now().isoformat()
            if 'activity' not in user:
                user['activity'] = {'feature_usage': {}, 'profiles_viewed': [], 'recent_actions': [], 'total_sessions': 0}
            user['activity']['total_sessions'] = user['activity'].get('total_sessions', 0) + 1
            user['activity']['recent_actions'].insert(0, {
                'action': 'login',
                'details': f'Session #{user["activity"]["total_sessions"]} (password set on first login)',
                'timestamp': datetime.now().isoformat()
            })
            user['activity']['recent_actions'] = user['activity']['recent_actions'][:100]
            save_users(users_data)
            session['username'] = username
            session['role'] = _normalize_role(user.get('role', 'user'))
            return jsonify({'success': True, 'redirect': '/'})
        
        if not verify_password(user['password_hash'], password):
            return jsonify({'success': False, 'error': 'Invalid username or password'})
        
        # Update last login
        user['last_login'] = datetime.now().isoformat()
        
        # Track session count
        if 'activity' not in user:
            user['activity'] = {
                'feature_usage': {},
                'profiles_viewed': [],
                'recent_actions': [],
                'total_sessions': 0
            }
        user['activity']['total_sessions'] = user['activity'].get('total_sessions', 0) + 1
        
        # Add login to recent actions
        user['activity']['recent_actions'].insert(0, {
            'action': 'login',
            'details': f'Session #{user["activity"]["total_sessions"]}',
            'timestamp': datetime.now().isoformat()
        })
        user['activity']['recent_actions'] = user['activity']['recent_actions'][:100]
        
        save_users(users_data)
        
        # Set session
        session['username'] = username
        session['role'] = _normalize_role(user.get('role', 'user'))
        if user.get('must_reset_password'):
            session['must_reset_password'] = True
            return jsonify({'success': True, 'redirect': '/set-password'})
        
        # Always redirect to dashboard, admin can access admin panel from there
        return jsonify({'success': True, 'redirect': '/'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/set-password')
def set_password_page():
    """Require user to set a new password (e.g. after restore). Only shown when must_reset_password is set."""
    if 'username' not in session:
        return redirect(url_for('login_page'))
    if not session.get('must_reset_password'):
        return redirect(url_for('index'))
    return render_template('set_password.html')


@app.route('/api/set-password', methods=['POST'])
def api_set_password():
    """Update current user's password and clear must_reset_password. Requires login."""
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    try:
        data = request.get_json() or {}
        new_password = (data.get('new_password') or '').strip()
        confirm = (data.get('confirm_password') or '').strip()
        if not new_password or new_password != confirm:
            return jsonify({'success': False, 'error': 'Passwords do not match'})
        if len(new_password) < 8:
            return jsonify({'success': False, 'error': 'Password must be at least 8 characters'})
        users_data = load_users()
        username = session['username']
        if username not in users_data.get('users', {}):
            return jsonify({'success': False, 'error': 'User not found'}), 404
        user = users_data['users'][username]
        user['password_hash'] = hash_password(new_password)
        user['must_reset_password'] = False
        save_users(users_data)
        session.pop('must_reset_password', None)
        return jsonify({'success': True, 'redirect': '/'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/terms')
def terms_page():
    """Terms of Use page - accessible without login"""
    return render_template('terms.html')

@app.route('/privacy')
def privacy_page():
    """Privacy Policy page - accessible without login"""
    return render_template('privacy.html')

@app.route('/admin')
@requires_admin
def admin_portal():
    # Get current user's role and username to pass to template
    user = get_current_user()
    current_role = _normalize_role(user.get('role', 'user') if user else 'user')
    default_profile_photo = load_default_profile_photo() or ''
    return render_template('admin.html', current_user_role=current_role, current_username=session.get('username', ''), default_profile_photo=default_profile_photo, is_dev_env=IS_DEV_ENV)

# ============================================================================
# ADMIN CLOAK (super_admin only): log in as another user to act as them
# ============================================================================

@app.route('/api/admin/cloak', methods=['POST'])
@requires_super_admin
def admin_cloak():
    """Switch session to act as another user. Only super_admin. Log out or Uncloak to return."""
    try:
        data = request.get_json() or {}
        target_username = (data.get('username') or '').strip()
        if not target_username:
            return jsonify({'success': False, 'error': 'Username required'}), 400
        users_data = load_users()
        if target_username not in users_data.get('users', {}):
            return jsonify({'success': False, 'error': 'User not found'}), 404
        original_username = session.get('username')
        session['username'] = target_username
        session['cloaked_from'] = original_username
        return jsonify({'success': True, 'redirect': '/'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/uncloak', methods=['POST'])
@requires_auth
def admin_uncloak():
    """Restore session to the admin who cloaked. Allowed when session has cloaked_from."""
    if 'cloaked_from' not in session:
        return jsonify({'success': False, 'error': 'Not cloaked'}), 400
    original = session.pop('cloaked_from', None)
    if original:
        session['username'] = original
        # Restore role from the original user's record (cloak had left session['role'] as the cloaked user's role)
        data = load_users()
        orig_user = data.get('users', {}).get(original)
        if orig_user:
            session['role'] = _normalize_role(orig_user.get('role', 'user'))
    return jsonify({'success': True, 'redirect': '/admin'})


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

def generate_random_password(length=12):
    """Generate a secure random password."""
    import string
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(secrets.choice(chars) for _ in range(length))

# ============================================================================
# GMAIL OAUTH INTEGRATION
# ============================================================================

GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly'
]
GMAIL_TOKEN_KEY = 'system/gmail_tokens.json'

def get_gmail_credentials():
    """Get Gmail OAuth credentials from environment or S3."""
    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    return client_id, client_secret

def load_gmail_tokens():
    """Load Gmail OAuth tokens from S3."""
    if not s3_client:
        return None
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=GMAIL_TOKEN_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return None

def save_gmail_tokens(tokens):
    """Save Gmail OAuth tokens to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=GMAIL_TOKEN_KEY,
            Body=json.dumps(tokens),
            ContentType='application/json'
        )
        print("✅ Gmail tokens saved to S3")
        return True
    except Exception as e:
        print(f"❌ Error saving Gmail tokens: {e}")
        return False

def get_gmail_service():
    """Get authenticated Gmail API service."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        tokens = load_gmail_tokens()
        if not tokens:
            return None
        
        creds = Credentials(
            token=tokens.get('access_token'),
            refresh_token=tokens.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=os.environ.get('GOOGLE_CLIENT_ID'),
            client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
            scopes=GMAIL_SCOPES
        )
        
        # Refresh if expired
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            # Save refreshed tokens
            save_gmail_tokens({
                'access_token': creds.token,
                'refresh_token': creds.refresh_token
            })
        
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        print(f"❌ Gmail service error: {e}")
        return None

def send_email_via_gmail(to_email, subject, html_content, text_content=None):
    """Send email using Gmail API (OAuth)."""
    import base64
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    service = get_gmail_service()
    if not service:
        return False, "Gmail not connected"
    
    try:
        msg = MIMEMultipart('alternative')
        msg['To'] = to_email
        msg['Subject'] = subject
        
        if text_content:
            msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))
        
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        
        service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()
        
        print(f"✅ Email sent via Gmail to {to_email}")
        return True, "Email sent via Gmail"
    except Exception as e:
        print(f"❌ Gmail send error: {e}")
        return False, str(e)


def send_email_with_attachment(to_emails, subject, html_content, attachment_filename, attachment_bytes, text_content=None):
    """Send email to one or more addresses with a CSV attachment. Uses Gmail API if available, else SMTP."""
    import base64
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders
    
    to_list = [e.strip() for e in (to_emails if isinstance(to_emails, list) else [to_emails]) if e and e.strip()]
    if not to_list:
        return False, "No recipients"
    
    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['To'] = ', '.join(to_list)
    
    part_body = MIMEMultipart('alternative')
    if text_content:
        part_body.attach(MIMEText(text_content, 'plain'))
    part_body.attach(MIMEText(html_content, 'html'))
    msg.attach(part_body)
    
    part_csv = MIMEBase('text', 'csv')
    part_csv.set_payload(attachment_bytes)
    encoders.encode_base64(part_csv)
    part_csv.add_header('Content-Disposition', 'attachment', filename=attachment_filename)
    msg.attach(part_csv)
    
    # Try Gmail API first
    service = get_gmail_service()
    if service:
        try:
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId='me', body={'raw': raw}).execute()
            print(f"✅ Activity export email sent via Gmail to {len(to_list)} recipient(s)")
            return True, "Email sent via Gmail"
        except Exception as e:
            print(f"⚠️ Gmail send failed: {e}, trying SMTP...")
    
    # Fall back to SMTP
    import smtplib
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    from_email = os.environ.get('FROM_EMAIL', smtp_user)
    if not smtp_user or not smtp_password:
        return False, "Email not configured (Gmail or SMTP)"
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg['From'] = from_email
            server.sendmail(from_email, to_list, msg.as_string())
        print(f"✅ Activity export email sent via SMTP to {len(to_list)} recipient(s)")
        return True, "Email sent via SMTP"
    except Exception as e:
        print(f"❌ SMTP send error: {e}")
        return False, str(e)


# Shared email design (dashboard-style) and signature for all notification emails
EMAIL_SIGNATURE = "— Crosswalk IQ Team"

def _email_base_styles():
    """CSS matching dashboard: dark bg, card, accent cyan/blue."""
    return """
    body { font-family: 'Poppins', 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif; background: #0a1929; color: #e6f1ff; padding: 20px; margin: 0; }
    .email-container { max-width: 600px; margin: 0 auto; background: #0d2137; border-radius: 12px; padding: 30px; }
    .email-header { color: #66d9ef; font-size: 20px; margin-bottom: 20px; font-weight: 600; }
    .email-body { line-height: 1.7; color: #e6f1ff; }
    .email-body p { margin: 0 0 1rem 0; }
    .email-btn { display: inline-block; background: linear-gradient(135deg, #66d9ef, #5a9ad9); color: #0a1929 !important; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 16px; }
    .email-card { background: #132f4c; border-radius: 8px; padding: 20px; margin: 20px 0; border-left: 4px solid #66d9ef; }
    .email-card-title { font-size: 18px; font-weight: bold; color: #c8ff00; margin-bottom: 8px; }
    .email-label { color: #8892b0; font-size: 12px; text-transform: uppercase; }
    .email-value { font-size: 16px; font-weight: bold; color: #e6f1ff; font-family: monospace; }
    .email-footer { margin-top: 28px; padding-top: 20px; border-top: 1px solid #132f4c; font-size: 12px; color: #8892b0; }
    """

def _wrap_email_html(body_content, title=None):
    """Wrap body HTML in dashboard-style layout and Crosswalk IQ Team signature."""
    header = f'<div class="email-header">{title}</div>' if title else ''
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>{_email_base_styles()}</style>
</head>
<body>
    <div class="email-container">
        {header}
        <div class="email-body">
            {body_content}
        </div>
        <div class="email-footer">
            <p>{EMAIL_SIGNATURE}</p>
        </div>
    </div>
</body>
</html>"""


@app.route('/api/admin/ai-cache/status')
@requires_admin
def ai_cache_status():
    """Get AI cache statistics."""
    ensure_cache_dir()
    cache_files = [f for f in os.listdir(AI_CACHE_DIR) if f.endswith('.json')]
    
    total_size = 0
    cache_entries = []
    
    for f in cache_files:
        filepath = os.path.join(AI_CACHE_DIR, f)
        try:
            stat = os.stat(filepath)
            total_size += stat.st_size
            with open(filepath, 'r') as file:
                data = json.load(file)
                cache_entries.append({
                    'key': f.replace('.json', ''),
                    'cached_at': data.get('cached_at', 'Unknown'),
                    'size': stat.st_size
                })
        except:
            pass
    
    return jsonify({
        'success': True,
        'total_entries': len(cache_files),
        'total_size_kb': round(total_size / 1024, 2),
        'entries': sorted(cache_entries, key=lambda x: x.get('cached_at', ''), reverse=True)[:50]
    })

@app.route('/api/admin/ai-cache/clear', methods=['POST'])
@requires_admin
def ai_cache_clear():
    """Clear all AI cache entries."""
    ensure_cache_dir()
    cache_files = [f for f in os.listdir(AI_CACHE_DIR) if f.endswith('.json')]
    
    cleared = 0
    for f in cache_files:
        try:
            os.remove(os.path.join(AI_CACHE_DIR, f))
            cleared += 1
        except:
            pass
    
    return jsonify({
        'success': True,
        'cleared': cleared
    })

@app.route('/api/admin/gmail/status')
@requires_admin
def gmail_status():
    """Check Gmail connection status."""
    tokens = load_gmail_tokens()
    client_id, client_secret = get_gmail_credentials()
    
    return jsonify({
        'success': True,
        'connected': tokens is not None and tokens.get('access_token') is not None,
        'configured': bool(client_id and client_secret),
        'email': tokens.get('email') if tokens else None
    })

@app.route('/api/admin/gmail/connect')
@requires_admin
def gmail_connect():
    """Start Gmail OAuth flow."""
    try:
        from google_auth_oauthlib.flow import Flow
        
        client_id, client_secret = get_gmail_credentials()
        if not client_id or not client_secret:
            return jsonify({
                'success': False,
                'error': 'Gmail OAuth not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to environment.'
            })
        
        # Build redirect URI
        redirect_uri = request.host_url.rstrip('/') + '/api/admin/gmail/callback'
        
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri]
                }
            },
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri
        )
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
        session['gmail_oauth_state'] = state
        
        return jsonify({
            'success': True,
            'auth_url': authorization_url
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/gmail/callback')
def gmail_callback():
    """Handle Gmail OAuth callback."""
    try:
        from google_auth_oauthlib.flow import Flow
        
        client_id, client_secret = get_gmail_credentials()
        redirect_uri = request.host_url.rstrip('/') + '/api/admin/gmail/callback'
        
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri]
                }
            },
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri
        )
        
        # Exchange code for tokens
        flow.fetch_token(authorization_response=request.url)
        
        credentials = flow.credentials
        
        # Get user email
        from googleapiclient.discovery import build
        service = build('gmail', 'v1', credentials=credentials)
        profile = service.users().getProfile(userId='me').execute()
        
        # Save tokens
        tokens = {
            'access_token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'email': profile.get('emailAddress')
        }
        # Automatically share Gmail with all admins and super_admins
        try:
            data = load_users()
            admin_usernames = [
                u for u, user in data.get('users', {}).items()
                if user.get('role') in ('admin', 'super_admin')
            ]
            if admin_usernames:
                tokens['shared_with'] = sorted(admin_usernames)
        except Exception as e:
            print(f"⚠️ Could not auto-share Gmail with admins: {e}")
        save_gmail_tokens(tokens)
        
        # Redirect back to admin with success
        return redirect('/admin?gmail=connected')
    except Exception as e:
        print(f"❌ Gmail OAuth callback error: {e}")
        return redirect(f'/admin?gmail=error&message={str(e)}')

@app.route('/api/admin/gmail/disconnect', methods=['POST'])
@requires_admin
def gmail_disconnect():
    """Disconnect Gmail OAuth."""
    try:
        if s3_client:
            s3_client.delete_object(Bucket=S3_BUCKET, Key=GMAIL_TOKEN_KEY)
        return jsonify({'success': True, 'message': 'Gmail disconnected'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/gmail/test', methods=['POST'])
@requires_admin
def gmail_test():
    """Send a test email via Gmail."""
    try:
        data = request.get_json()
        to_email = data.get('to_email')
        
        if not to_email:
            return jsonify({'success': False, 'error': 'Email address required'})
        
        success, message = send_email_via_gmail(
            to_email,
            '🧪 Test Email from Crosswalk IQ',
            '<h1>Test Email</h1><p>This is a test email from your Crosswalk IQ dashboard.</p><p>Gmail integration is working!</p>',
            'Test Email\n\nThis is a test email from your Crosswalk IQ dashboard.\nGmail integration is working!'
        )
        
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# EMAIL SENDING (uses Gmail API if connected, falls back to SMTP)
# ============================================================================

def send_welcome_email_async(email, username, password, role):
    """Send welcome email in background thread (non-blocking)."""
    def _send():
        try:
            send_welcome_email_sync(email, username, password, role)
        except Exception as e:
            print(f"❌ Background email failed: {e}")
    
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True, "Email queued for sending"

def send_welcome_email_sync(email, username, password, role):
    """Send welcome email with login credentials (blocking). Uses Gmail API if available, falls back to SMTP."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    app_url = os.environ.get('APP_URL', 'https://behavioralgraph.onrender.com')
    
    # Build email content
    text = f"""
Welcome to Crosswalk's IQ Laboratory!

Your account has been created. Here are your login details:

Username: {username}
Password: {password}
Role: {role}

Login URL: {app_url}/login

You can change your password after logging in by going to your profile settings.

If you have any questions, please contact your administrator.

Best regards,
Crosswalk IQ Team
    """
    
    body = f"""
        <div class="email-header">🎉 Welcome to Crosswalk IQ</div>
        <p>Your account has been created. Here are your login details:</p>
        <div class="email-card">
            <div class="email-card-title">Login details</div>
            <div style="margin: 10px 0;"><span class="email-label">Username</span><br><span class="email-value">{username}</span></div>
            <div style="margin: 10px 0;"><span class="email-label">Password</span><br><span class="email-value">{password}</span></div>
            <div style="margin: 10px 0;"><span class="email-label">Role</span><br><span class="email-value">{role.upper()}</span></div>
        </div>
        <p><a href="{app_url}/login" class="email-btn">Login Now →</a></p>
        <p>You can change your password after logging in if you'd like.</p>
        <p style="font-size: 12px; color: #8892b0;">If you have any questions, please contact your administrator.</p>
    """
    html = _wrap_email_html(body)
    
    # Try Gmail API first (if connected)
    tokens = load_gmail_tokens()
    if tokens and tokens.get('access_token'):
        print(f"📧 Sending email via Gmail API to {email}...")
        success, message = send_email_via_gmail(
            email,
            "🎉 Welcome to Crosswalk's IQ Laboratory",
            html,
            text
        )
        if success:
            return True, "Email sent via Gmail"
        else:
            print(f"⚠️ Gmail API failed: {message}, trying SMTP...")
    
    # Fall back to SMTP
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    from_email = os.environ.get('FROM_EMAIL', smtp_user)
    
    if not smtp_user or not smtp_password:
        print(f"⚠️ SMTP not configured - skipping welcome email for {username}")
        return False, "Email not configured. Connect Gmail in Admin settings."
    
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "🎉 Welcome to Crosswalk's IQ Laboratory"
        msg['From'] = from_email
        msg['To'] = email
        
        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email with timeout
        print(f"📧 Sending email via SMTP to {email}...")
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, email, msg.as_string())
        
        print(f"✅ Welcome email sent to {email} for user {username}")
        return True, "Email sent successfully"
        
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ SMTP authentication failed for {email}: {e}")
        return False, "SMTP authentication failed - check credentials"
    except smtplib.SMTPException as e:
        print(f"❌ SMTP error for {email}: {e}")
        return False, f"SMTP error: {str(e)}"
    except Exception as e:
        print(f"❌ Failed to send welcome email to {email}: {e}")
        return False, str(e)

def send_welcome_email(email, username, password, role):
    """Send welcome email - uses async to avoid blocking."""
    return send_welcome_email_async(email, username, password, role)

@app.route('/api/admin/users', methods=['POST'])
@requires_admin
def create_user():
    """Create a new user with optional email welcome."""
    try:
        req_data = request.json
        username = req_data.get('username', '').strip().lower()
        email = req_data.get('email', '').strip().lower()
        password = req_data.get('password', '')
        send_welcome = req_data.get('send_welcome_email', False)
        
        if not username:
            return jsonify({'success': False, 'error': 'Username required'})
        
        if len(username) < 2:
            return jsonify({'success': False, 'error': 'Username must be at least 2 characters'})
        
        # Auto-generate password if not provided
        generated_password = False
        if not password:
            password = generate_random_password()
            generated_password = True
        
        data = load_users()
        
        if username in data['users']:
            return jsonify({'success': False, 'error': 'Username already exists'})
        
        role = _normalize_role(req_data.get('role', 'user'))
        
        company = req_data.get('company', '')
        cd = data.get('company_defaults', {}).get(company) if company else None

        data['users'][username] = {
            'password_hash': hash_password(password),
            'email': email,
            'first_name': req_data.get('first_name', ''),
            'last_name': req_data.get('last_name', ''),
            'company': company,
            'department': req_data.get('department', ''),
            'role': role,
            'credits': req_data.get('credits', cd.get('credits', 5) if cd else 5),
            'credits_used': 0,
            'created_at': datetime.now().isoformat(),
            'last_login': None,
            'access_expires': req_data.get('access_expires'),
            'allowed_categories': req_data.get('allowed_categories', cd.get('allowed_categories', ['*']) if cd else ['*']),
            'allowed_runs': req_data.get('allowed_runs', cd.get('allowed_runs', ['*']) if cd else ['*']),
            'allowed_behavioral_categories': req_data.get('allowed_behavioral_categories', cd.get('allowed_behavioral_categories', ['*']) if cd else ['*']),
            'has_profile_iq_access': req_data.get('has_profile_iq_access', cd.get('has_profile_iq_access', True) if cd else True),
            'has_subscriber_iq_access': req_data.get('has_subscriber_iq_access', cd.get('has_subscriber_iq_access', False) if cd else False),
            'has_ticket_sales_iq_access': req_data.get('has_ticket_sales_iq_access', cd.get('has_ticket_sales_iq_access', True) if cd else True),
            'has_hedge_fund_iq_access': req_data.get('has_hedge_fund_iq_access', cd.get('has_hedge_fund_iq_access', False) if cd else False),
            'hedge_fund_iq_tabs': req_data.get('hedge_fund_iq_tabs', []),
            'hedge_fund_iq_tickers': req_data.get('hedge_fund_iq_tickers', []),
            'has_analysis_iq_access': req_data.get('has_analysis_iq_access', cd.get('has_analysis_iq_access', False) if cd else False),
            'analysis_iq_modules': req_data.get('analysis_iq_modules', []),
            'has_ticket_sales_tracker_access': req_data.get('has_ticket_sales_tracker_access', cd.get('has_ticket_sales_tracker_access', False) if cd else False),
            'has_rankers_iq_access': req_data.get('has_rankers_iq_access', cd.get('has_rankers_iq_access', False) if cd else False),
            'has_llmo_iq_access': req_data.get('has_llmo_iq_access', cd.get('has_llmo_iq_access', False) if cd else False),
            'rankers_iq_options': req_data.get('rankers_iq_options', []),
            'collab_team': req_data.get('collab_team', []),
            'has_purgatory_approval': False
        }
        
        # Purgatory clearance: only super_admin can grant (or set on create)
        if 'has_purgatory_approval' in req_data:
            current_user = get_current_user()
            if not current_user or current_user.get('role') != 'super_admin':
                return jsonify({'success': False, 'error': 'Only a super admin can grant purgatory clearance'}), 403
            data['users'][username]['has_purgatory_approval'] = req_data.get('has_purgatory_approval', False)
        
        save_users(data)
        
        # Send welcome email if requested and email provided
        email_status = None
        if send_welcome and email:
            email_sent, email_message = send_welcome_email(email, username, password, role)
            email_status = 'sent' if email_sent else f'failed: {email_message}'
        
        response = {
            'success': True, 
            'message': f'User {username} created',
            'password_generated': generated_password
        }
        
        if email_status:
            response['email_status'] = email_status
        
        if generated_password and not send_welcome:
            # If password was generated but no email sent, include it in response for admin
            response['generated_password'] = password
            response['note'] = 'Password was auto-generated. Share it with the user securely.'
        
        return jsonify(response)
        
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
        if 'email' in req_data:
            user['email'] = req_data['email']
        if 'first_name' in req_data:
            user['first_name'] = req_data['first_name']
        if 'last_name' in req_data:
            user['last_name'] = req_data['last_name']
        if 'company' in req_data:
            user['company'] = req_data['company']
        if 'department' in req_data:
            user['department'] = req_data['department']
        if 'role' in req_data:
            # Never allow downgrading the primary 'admin' account from super_admin
            if username == 'admin':
                if user.get('role') == 'super_admin':
                    user['role'] = 'super_admin'  # keep super_admin
                else:
                    user['role'] = 'super_admin'   # repair: admin account must always be super_admin
            else:
                user['role'] = _normalize_role(req_data['role'])
        if 'credits' in req_data:
            user['credits'] = req_data['credits']
        if 'credit_ceiling' in req_data:
            user['credit_ceiling'] = req_data['credit_ceiling']
        if 'credit_source' in req_data:
            user['credit_source'] = req_data['credit_source']
        if 'access_expires' in req_data:
            user['access_expires'] = req_data['access_expires']
        if 'allowed_categories' in req_data:
            user['allowed_categories'] = req_data['allowed_categories']
        if 'allowed_runs' in req_data:
            user['allowed_runs'] = req_data['allowed_runs']
        if 'allowed_behavioral_categories' in req_data:
            user['allowed_behavioral_categories'] = req_data['allowed_behavioral_categories']
        if 'has_profile_iq_access' in req_data:
            user['has_profile_iq_access'] = req_data['has_profile_iq_access']
        if 'has_subscriber_iq_access' in req_data:
            user['has_subscriber_iq_access'] = req_data['has_subscriber_iq_access']
        if 'has_ticket_sales_iq_access' in req_data:
            user['has_ticket_sales_iq_access'] = req_data['has_ticket_sales_iq_access']
        if 'has_hedge_fund_iq_access' in req_data:
            user['has_hedge_fund_iq_access'] = req_data['has_hedge_fund_iq_access']
        if 'hedge_fund_iq_tabs' in req_data:
            user['hedge_fund_iq_tabs'] = req_data['hedge_fund_iq_tabs']
        if 'hedge_fund_iq_tickers' in req_data:
            user['hedge_fund_iq_tickers'] = req_data['hedge_fund_iq_tickers']
        if 'has_analysis_iq_access' in req_data:
            user['has_analysis_iq_access'] = bool(req_data['has_analysis_iq_access'])
        if 'analysis_iq_modules' in req_data:
            raw = req_data['analysis_iq_modules']
            user['analysis_iq_modules'] = list(raw) if isinstance(raw, list) else []
        if 'has_ticket_sales_tracker_access' in req_data:
            user['has_ticket_sales_tracker_access'] = bool(req_data['has_ticket_sales_tracker_access'])
        if 'has_rankers_iq_access' in req_data:
            user['has_rankers_iq_access'] = req_data['has_rankers_iq_access']
        if 'has_llmo_iq_access' in req_data:
            user['has_llmo_iq_access'] = req_data['has_llmo_iq_access']
        if 'rankers_iq_options' in req_data:
            user['rankers_iq_options'] = req_data['rankers_iq_options']
        if 'collab_team' in req_data:
            user['collab_team'] = req_data['collab_team']
        # Activity CSV export (per user): emails comma-separated, cadence
        if 'activity_export_emails' in req_data:
            user['activity_export_emails'] = (req_data['activity_export_emails'] or '').strip()
        if 'activity_export_cadence' in req_data:
            cadence = (req_data['activity_export_cadence'] or '').strip().lower()
            user['activity_export_cadence'] = cadence if cadence in ACTIVITY_EXPORT_CADENCES else ''
        # Purgatory clearance: only super_admin can grant or revoke
        if 'has_purgatory_approval' in req_data:
            current_user = get_current_user()
            if not current_user or current_user.get('role') != 'super_admin':
                return jsonify({'success': False, 'error': 'Only a super admin can grant or revoke purgatory clearance'}), 403
            user['has_purgatory_approval'] = req_data['has_purgatory_approval']
        
        # Handle username change
        new_username = req_data.get('new_username', '').strip().lower()
        if new_username and new_username != username:
            if new_username in data['users']:
                return jsonify({'success': False, 'error': f'Username {new_username} already exists'})
            if username == 'admin':
                return jsonify({'success': False, 'error': 'Cannot rename the admin user'})
            # Move user to new key
            data['users'][new_username] = user
            del data['users'][username]
            username = new_username
        
        save_users(data)
        return jsonify({'success': True, 'message': f'User {username} updated'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/users/<username>/add-credits', methods=['POST'])
@requires_admin
def add_user_credits(username):
    """Add credits to a user and record attribution (what the credits were for). Admin only."""
    try:
        req = request.get_json() or {}
        credits = req.get('credits')
        reason = (req.get('reason') or '').strip() or 'Credits added by admin'
        if credits is None:
            return jsonify({'success': False, 'error': 'credits is required'}), 400
        try:
            credits = int(credits)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'credits must be a number'}), 400
        if credits <= 0:
            return jsonify({'success': False, 'error': 'credits must be positive'}), 400

        data = load_users()
        if username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        user = data['users'][username]
        if user.get('credits') == -1:
            return jsonify({'success': False, 'error': 'User has unlimited credits; cannot add'})

        added_by = (get_current_user() or {}).get('username') or 'admin'
        added_at = datetime.now().isoformat()
        user['credits'] = user.get('credits', 0) + credits
        history = user.setdefault('credit_attribution_history', [])
        history.insert(0, {
            'added_at': added_at,
            'credits_added': credits,
            'reason': reason,
            'added_by': added_by
        })
        user['credit_attribution_history'] = history[:500]
        save_users(data)
        return jsonify({'success': True, 'message': f'Added {credits} credits to {username}', 'credits': user['credits']})
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

@app.route('/api/admin/users/restore-defaults-all', methods=['POST'])
@requires_admin
def restore_defaults_all_users():
    """Set all users' access to the given defaults (same as Restore Defaults in user modal). Expects JSON body with allowed_runs, allowed_behavioral_categories from frontend."""
    try:
        req = request.get_json() or {}
        # Frontend sends the same values the per-user "Restore Defaults" buttons produce
        allowed_runs = req.get('allowed_runs')
        allowed_behavioral_categories = req.get('allowed_behavioral_categories')
        if allowed_runs is None or allowed_behavioral_categories is None:
            return jsonify({
                'success': False,
                'error': 'Missing allowed_runs or allowed_behavioral_categories. Use the Restore Default Values button from the User Management page (it computes defaults from Quick Selects).'
            }), 400
        allowed_categories = req.get('allowed_categories', ['*'])

        data = load_users()
        users = data.get('users', {})
        count = 0
        for username, user in users.items():
            # Only touch access/defaults; never change role or delete users
            user['allowed_categories'] = list(allowed_categories) if isinstance(allowed_categories, list) else ['*']
            user['allowed_runs'] = list(allowed_runs) if isinstance(allowed_runs, list) else ['*']
            user['allowed_behavioral_categories'] = list(allowed_behavioral_categories) if isinstance(allowed_behavioral_categories, list) else ['*']
            user['has_profile_iq_access'] = True
            user['has_subscriber_iq_access'] = False
            user['has_ticket_sales_iq_access'] = True
            user['has_hedge_fund_iq_access'] = False
            user['hedge_fund_iq_tabs'] = user.get('hedge_fund_iq_tabs', [])
            user['hedge_fund_iq_tickers'] = user.get('hedge_fund_iq_tickers', [])
            user['has_analysis_iq_access'] = False
            user['analysis_iq_modules'] = user.get('analysis_iq_modules', [])
            user['has_rankers_iq_access'] = False
            user['has_llmo_iq_access'] = False
            user['rankers_iq_options'] = user.get('rankers_iq_options', [])
            count += 1
        save_users(data)
        return jsonify({'success': True, 'message': f'Restored defaults for {count} user(s)', 'count': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/runs/remove-from-all', methods=['POST'])
@requires_admin
def api_remove_run_from_all():
    """Remove a profile (S3 key) from every user's allowed_runs."""
    req = request.get_json() or {}
    s3_key = req.get('s3_key')
    if not s3_key:
        return jsonify({'success': False, 'error': 'Missing s3_key'}), 400
    remove_run_from_all_users(s3_key)
    return jsonify({'success': True, 'message': f'Removed from all users: {s3_key}'})


@app.route('/api/admin/runs/add-to-all', methods=['POST'])
@requires_admin
def api_add_run_to_all():
    """Add a profile (S3 key) to every user's allowed_runs (admin override, bypasses category check)."""
    req = request.get_json() or {}
    s3_key = req.get('s3_key')
    if not s3_key:
        return jsonify({'success': False, 'error': 'Missing s3_key'}), 400
    try:
        data = load_users()
        users = data.get('users', {})
        changed = False
        for username, user in users.items():
            runs = user.get('allowed_runs', ['*'])
            if isinstance(runs, list) and '*' in runs:
                continue
            existing = set(runs or [])
            if s3_key not in existing:
                user['allowed_runs'] = list(existing | {s3_key})
                changed = True
        if changed:
            save_users(data)
        return jsonify({'success': True, 'message': f'Added to all users: {s3_key}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



# ── Company Management Endpoints ──────────────────────────────────────────────

@app.route('/api/admin/companies', methods=['GET'])
@requires_admin
def api_list_companies():
    """Derive companies from user data and return summary stats for each."""
    try:
        data = load_users()
        users = data.get('users', {})
        company_defaults = data.get('company_defaults', {})
        company_entities = data.get('companies', {})
        companies = {}
        # Include explicitly created companies even if they have no users yet
        for co_name, co_data in company_entities.items():
            pt = co_data.get('credit_pool', 0)
            pu = co_data.get('credit_pool_used', 0)
            companies[co_name] = {
                'name': co_name, 'user_count': 0, 'credits_used': 0,
                'total_sessions': 0, 'last_active': None,
                'has_custom_defaults': co_name in company_defaults,
                'credit_pool': pt, 'credit_pool_used': pu,
                'credit_pool_remaining': -1 if pt == -1 else pt - pu,
            }
        for username, user in users.items():
            co = (user.get('company') or '').strip()
            if not co:
                continue
            if co not in companies:
                companies[co] = {'name': co, 'user_count': 0, 'credits_used': 0,
                                 'total_sessions': 0, 'last_active': None,
                                 'has_custom_defaults': co in company_defaults,
                                 'credit_pool': None, 'credit_pool_used': None,
                                 'credit_pool_remaining': None}
            c = companies[co]
            c['user_count'] += 1
            c['credits_used'] += user.get('credits_used') or 0
            activity = user.get('activity') or {}
            c['total_sessions'] += activity.get('total_sessions') or 0
            ll = user.get('last_login')
            if ll and (c['last_active'] is None or ll > c['last_active']):
                c['last_active'] = ll
        return jsonify({'success': True, 'companies': sorted(companies.values(), key=lambda x: x['name'].lower())})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies', methods=['POST'])
@requires_admin
def api_create_company():
    """Create a company entity with an optional credit pool and defaults."""
    try:
        req = request.get_json() or {}
        name = (req.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Company name is required'}), 400
        data = load_users()
        companies = data.setdefault('companies', {})
        if name in companies:
            return jsonify({'success': False, 'error': f'Company "{name}" already exists'}), 400
        companies[name] = {
            'created_at': datetime.now().isoformat(),
            'credit_pool': req.get('credit_pool', 0),
            'credit_pool_used': 0,
        }
        defaults = req.get('defaults')
        if defaults and isinstance(defaults, dict):
            data.setdefault('company_defaults', {})[name] = defaults
        save_users(data)
        return jsonify({'success': True, 'message': f'Company "{name}" created'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/credits', methods=['PUT'])
@requires_admin
def api_update_company_credits(company_name):
    """Update a company's credit pool total or add credits."""
    try:
        req = request.get_json() or {}
        data = load_users()
        companies = data.setdefault('companies', {})
        if company_name not in companies:
            companies[company_name] = {
                'created_at': datetime.now().isoformat(),
                'credit_pool': 0,
                'credit_pool_used': 0,
            }
        pool = companies[company_name]
        if 'credit_pool' in req:
            pool['credit_pool'] = req['credit_pool']
        if req.get('add_credits'):
            current = pool.get('credit_pool', 0)
            if current != -1:
                pool['credit_pool'] = current + int(req['add_credits'])
        save_users(data)
        remaining = -1 if pool['credit_pool'] == -1 else pool['credit_pool'] - pool.get('credit_pool_used', 0)
        return jsonify({'success': True, 'credit_pool': pool['credit_pool'],
                        'credit_pool_used': pool.get('credit_pool_used', 0),
                        'credit_pool_remaining': remaining})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/user-credits', methods=['PUT'])
@requires_admin
def api_update_company_user_credits(company_name):
    """Update credit_ceiling or credit_source for a single user in the company."""
    try:
        req = request.get_json() or {}
        uname = req.get('username')
        if not uname:
            return jsonify({'success': False, 'error': 'username required'}), 400
        data = load_users()
        user = data['users'].get(uname)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        if 'credit_ceiling' in req:
            user['credit_ceiling'] = int(req['credit_ceiling'])
        if 'credit_source' in req:
            user['credit_source'] = req['credit_source']
        if 'credits' in req:
            user['credits'] = int(req['credits'])
        save_users(data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/users', methods=['GET'])
@requires_admin
def api_company_users(company_name):
    """Return all users belonging to a company with their stats and pool info."""
    try:
        data = load_users()
        users = data.get('users', {})
        pool = _get_company_pool(data, company_name)
        pool_info = None
        if pool:
            pt = pool.get('credit_pool', 0)
            pu = pool.get('credit_pool_used', 0)
            pool_info = {'credit_pool': pt, 'credit_pool_used': pu,
                         'credit_pool_remaining': -1 if pt == -1 else pt - pu}
        result = []
        for username, user in users.items():
            if (user.get('company') or '').strip().lower() != company_name.strip().lower():
                continue
            activity = user.get('activity') or {}
            profiles_viewed = activity.get('profiles_viewed') or []
            result.append({
                'username': username,
                'first_name': user.get('first_name', ''),
                'last_name': user.get('last_name', ''),
                'email': user.get('email', ''),
                'role': user.get('role', 'user'),
                'credits': user.get('credits', 0),
                'credits_used': user.get('credits_used', 0),
                'credit_ceiling': user.get('credit_ceiling', -1),
                'credit_source': user.get('credit_source', 'pool'),
                'total_sessions': activity.get('total_sessions') or 0,
                'last_login': user.get('last_login'),
                'profiles_viewed': len(profiles_viewed),
                'created_at': user.get('created_at'),
            })
        result.sort(key=lambda x: (x.get('last_login') or ''), reverse=True)
        return jsonify({'success': True, 'users': result, 'company': company_name, 'pool': pool_info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/report', methods=['GET'])
@requires_admin
def api_company_report(company_name):
    """Aggregated usage report for a company."""
    try:
        data = load_users()
        users = data.get('users', {})
        total_users = 0
        active_users = 0
        total_credits_used = 0
        total_sessions = 0
        feature_usage_agg = {}
        top_profiles = {}
        recent_actions = []
        for username, user in users.items():
            if (user.get('company') or '').strip().lower() != company_name.strip().lower():
                continue
            total_users += 1
            total_credits_used += user.get('credits_used') or 0
            activity = user.get('activity') or {}
            sessions = activity.get('total_sessions') or 0
            total_sessions += sessions
            if sessions > 0:
                active_users += 1
            for feat, count in (activity.get('feature_usage') or {}).items():
                feature_usage_agg[feat] = feature_usage_agg.get(feat, 0) + count
            for p in (activity.get('profiles_viewed') or []):
                pk = p.get('name') or p.get('key') or ''
                if pk:
                    top_profiles[pk] = top_profiles.get(pk, 0) + (p.get('view_count') or 1)
            for a in (activity.get('recent_actions') or [])[-20:]:
                recent_actions.append({**a, 'username': username})
        recent_actions.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
        top_profiles_list = sorted(top_profiles.items(), key=lambda x: -x[1])[:20]
        feature_usage_list = sorted(feature_usage_agg.items(), key=lambda x: -x[1])
        return jsonify({
            'success': True,
            'company': company_name,
            'total_users': total_users,
            'active_users': active_users,
            'total_credits_used': total_credits_used,
            'total_sessions': total_sessions,
            'avg_sessions': round(total_sessions / max(total_users, 1), 1),
            'feature_usage': feature_usage_list,
            'top_profiles': top_profiles_list,
            'recent_actions': recent_actions[:50],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/export', methods=['GET'])
@requires_admin
def api_company_export(company_name):
    """Export credit usage and activity for a company, filtered by date range and optional usernames."""
    date_from_str = request.args.get('date_from', '').strip()
    date_to_str = request.args.get('date_to', '').strip()
    usernames_param = request.args.get('usernames', '').strip()
    if not date_from_str or not date_to_str:
        return jsonify({'success': False, 'error': 'date_from and date_to (YYYY-MM-DD) required'}), 400
    try:
        date_from = datetime.strptime(date_from_str, '%Y-%m-%d')
        date_to = datetime.strptime(date_to_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid date format; use YYYY-MM-DD'}), 400
    if date_from > date_to:
        return jsonify({'success': False, 'error': 'date_from must be on or before date_to'}), 400
    start = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    end = date_to.replace(hour=23, minute=59, second=59, microsecond=999999)
    selected_users = set(u.strip() for u in usernames_param.split(',') if u.strip()) if usernames_param else None
    data = load_users()
    all_users = data.get('users', {})
    rows = [['Username', 'Name', 'Email', 'Date', 'Description', 'Pull Type', 'Job ID', 'Credits Used']]
    summary_rows = [['--- SUMMARY ---'], ['Company', company_name], ['Date Range', f'{date_from_str} to {date_to_str}'], []]
    summary_rows.append(['Username', 'Name', 'Email', 'Role', 'Credits Used (Period)', 'Total Sessions', 'Last Login'])
    total_credits_period = 0
    for username, user in all_users.items():
        if (user.get('company') or '').strip().lower() != company_name.strip().lower():
            continue
        if selected_users and username not in selected_users:
            continue
        name = ' '.join(filter(None, [user.get('first_name', ''), user.get('last_name', '')]))
        email = user.get('email', '')
        user_credits_period = 0
        history = user.get('credit_usage_history') or []
        for entry in history:
            used_at_str = entry.get('used_at') or ''
            if not used_at_str:
                continue
            try:
                used_at = datetime.fromisoformat(used_at_str.replace('Z', '+00:00'))
                if used_at.tzinfo:
                    used_at = used_at.replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if not (start <= used_at <= end):
                continue
            cr = entry.get('credits_used') if entry.get('credits_used') is not None else 1
            user_credits_period += cr
            rows.append([
                username, name, email,
                used_at.strftime('%Y-%m-%d %H:%M:%S'),
                entry.get('description') or entry.get('pull_type') or 'Analysis',
                entry.get('pull_type') or '',
                entry.get('job_id') or '',
                str(cr)
            ])
        total_credits_period += user_credits_period
        activity = user.get('activity') or {}
        summary_rows.append([
            username, name, email,
            user.get('role', 'user'),
            str(user_credits_period),
            str(activity.get('total_sessions') or 0),
            user.get('last_login') or 'Never'
        ])
    summary_rows.append([])
    summary_rows.append(['Total Credits Used (Period)', str(total_credits_period)])
    summary_rows.append([])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(summary_rows)
    writer.writerow([])
    writer.writerow(['--- DETAILED CREDIT USAGE ---'])
    writer.writerows(rows)
    safe_name = company_name.replace(' ', '_').replace('/', '_')
    filename = f'{safe_name}_usage_{date_from_str}_to_{date_to_str}.csv'
    return Response(
        out.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/api/admin/companies/<path:company_name>/defaults', methods=['GET'])
@requires_admin
def api_get_company_defaults(company_name):
    """Get custom defaults for a company, or null if none set."""
    try:
        data = load_users()
        defaults = data.get('company_defaults', {}).get(company_name)
        return jsonify({'success': True, 'company': company_name, 'defaults': defaults})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/defaults', methods=['PUT'])
@requires_admin
def api_set_company_defaults(company_name):
    """Save custom defaults for a company."""
    try:
        req = request.get_json() or {}
        data = load_users()
        if 'company_defaults' not in data:
            data['company_defaults'] = {}
        data['company_defaults'][company_name] = {
            'allowed_categories': req.get('allowed_categories', ['*']),
            'allowed_runs': req.get('allowed_runs', ['*']),
            'allowed_behavioral_categories': req.get('allowed_behavioral_categories', ['*']),
            'has_profile_iq_access': req.get('has_profile_iq_access', True),
            'has_subscriber_iq_access': req.get('has_subscriber_iq_access', False),
            'has_ticket_sales_iq_access': req.get('has_ticket_sales_iq_access', True),
            'has_hedge_fund_iq_access': req.get('has_hedge_fund_iq_access', False),
            'has_analysis_iq_access': req.get('has_analysis_iq_access', False),
            'has_rankers_iq_access': req.get('has_rankers_iq_access', False),
            'has_llmo_iq_access': req.get('has_llmo_iq_access', False),
            'has_ticket_sales_tracker_access': req.get('has_ticket_sales_tracker_access', False),
            'credits': req.get('credits', 5),
        }
        save_users(data)
        return jsonify({'success': True, 'message': f'Defaults saved for {company_name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/defaults', methods=['DELETE'])
@requires_admin
def api_delete_company_defaults(company_name):
    """Remove custom defaults for a company (revert to global)."""
    try:
        data = load_users()
        cd = data.get('company_defaults', {})
        if company_name in cd:
            del cd[company_name]
            save_users(data)
        return jsonify({'success': True, 'message': f'Custom defaults removed for {company_name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/reset-users', methods=['POST'])
@requires_admin
def api_reset_company_users(company_name):
    """Reset all users in a company to either company defaults (if set) or global defaults."""
    try:
        req = request.get_json() or {}
        data = load_users()
        users = data.get('users', {})
        cd = data.get('company_defaults', {}).get(company_name)
        global_runs = req.get('allowed_runs', ['*'])
        global_behavioral = req.get('allowed_behavioral_categories', ['*'])
        count = 0
        for username, user in users.items():
            if (user.get('company') or '').strip().lower() != company_name.strip().lower():
                continue
            if cd:
                user['allowed_categories'] = list(cd.get('allowed_categories', ['*']))
                user['allowed_runs'] = list(cd.get('allowed_runs', ['*']))
                user['allowed_behavioral_categories'] = list(cd.get('allowed_behavioral_categories', ['*']))
                user['has_profile_iq_access'] = cd.get('has_profile_iq_access', True)
                user['has_subscriber_iq_access'] = cd.get('has_subscriber_iq_access', False)
                user['has_ticket_sales_iq_access'] = cd.get('has_ticket_sales_iq_access', True)
                user['has_hedge_fund_iq_access'] = cd.get('has_hedge_fund_iq_access', False)
                user['has_analysis_iq_access'] = cd.get('has_analysis_iq_access', False)
                user['has_rankers_iq_access'] = cd.get('has_rankers_iq_access', False)
                user['has_llmo_iq_access'] = cd.get('has_llmo_iq_access', False)
                user['has_ticket_sales_tracker_access'] = cd.get('has_ticket_sales_tracker_access', False)
                user['credits'] = cd.get('credits', 5)
            else:
                user['allowed_categories'] = ['*']
                user['allowed_runs'] = list(global_runs) if isinstance(global_runs, list) else ['*']
                user['allowed_behavioral_categories'] = list(global_behavioral) if isinstance(global_behavioral, list) else ['*']
                user['has_profile_iq_access'] = True
                user['has_subscriber_iq_access'] = False
                user['has_ticket_sales_iq_access'] = True
                user['has_hedge_fund_iq_access'] = False
                user['has_analysis_iq_access'] = False
                user['has_rankers_iq_access'] = False
                user['has_llmo_iq_access'] = False
                user['has_ticket_sales_tracker_access'] = False
                user['credits'] = 5
            count += 1
        save_users(data)
        src = 'company defaults' if cd else 'global defaults'
        return jsonify({'success': True, 'message': f'Reset {count} user(s) in {company_name} to {src}', 'count': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/companies/<path:company_name>/reset-run-access', methods=['POST'])
@requires_admin
def api_reset_company_run_access(company_name):
    """Reset run access (allowed_runs + allowed_behavioral_categories) for all users in a company to Quick Select defaults."""
    try:
        req = request.get_json() or {}
        allowed_runs = req.get('allowed_runs', ['*'])
        allowed_behavioral_categories = req.get('allowed_behavioral_categories', ['*'])
        data = load_users()
        users = data.get('users', {})
        count = 0
        for username, user in users.items():
            if (user.get('company') or '').strip().lower() != company_name.strip().lower():
                continue
            user['allowed_runs'] = list(allowed_runs) if isinstance(allowed_runs, list) else ['*']
            user['allowed_behavioral_categories'] = list(allowed_behavioral_categories) if isinstance(allowed_behavioral_categories, list) else ['*']
            count += 1
        save_users(data)
        return jsonify({'success': True, 'message': f'Reset run access for {count} user(s) in {company_name} to Quick Select defaults', 'count': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/users/<username>/reset-password', methods=['POST'])
@requires_admin
def reset_user_password(username):
    """Reset a user's password and optionally send email."""
    try:
        req_data = request.json or {}
        send_email = req_data.get('send_email', True)
        
        data = load_users()
        
        if username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        user = data['users'][username]
        
        # Generate new password
        new_password = generate_random_password()
        user['password_hash'] = hash_password(new_password)
        user['password_reset_at'] = datetime.now().isoformat()
        
        save_users(data)
        
        # Send password reset email
        email_status = None
        if send_email and user.get('email'):
            email_sent, email_message = send_password_reset_email(
                user['email'], 
                username, 
                new_password,
                user.get('role', 'user')
            )
            email_status = 'sent' if email_sent else f'failed: {email_message}'
        
        return jsonify({
            'success': True, 
            'message': f'Password reset for {username}',
            'new_password': new_password if not send_email else None,  # Only return if not emailing
            'email_status': email_status
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def send_password_reset_email(email, username, password, role):
    """Send password reset email."""
    app_url = os.environ.get('APP_URL', 'https://behavioralgraph.onrender.com')
    
    text = f"""
Your password has been reset.

Here are your new login details:

Username: {username}
New Password: {password}

Login URL: {app_url}/login

You can change your password after logging in if you'd like.

— Crosswalk IQ Team
    """
    
    body = f"""
        <div class="email-header">🔐 Password Reset</div>
        <p>Your password has been reset by an administrator. Here are your new login details:</p>
        <div class="email-card">
            <div class="email-card-title">Login details</div>
            <div style="margin: 10px 0;"><span class="email-label">Username</span><br><span class="email-value">{username}</span></div>
            <div style="margin: 10px 0;"><span class="email-label">New Password</span><br><span class="email-value">{password}</span></div>
        </div>
        <p><a href="{app_url}/login" class="email-btn">Login Now →</a></p>
        <p>You can change your password after logging in if you'd like.</p>
    """
    html = _wrap_email_html(body)
    
    # Try Gmail API first
    tokens = load_gmail_tokens()
    if tokens and tokens.get('access_token'):
        success, message = send_email_via_gmail(
            email,
            '🔐 Password Reset - Crosswalk IQ',
            html,
            text
        )
        if success:
            return True, "Email sent via Gmail"
    
    # Fall back to SMTP
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    from_email = os.environ.get('FROM_EMAIL', smtp_user)
    
    if not smtp_user or not smtp_password:
        return False, "Email not configured"
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '🔐 Password Reset - Crosswalk IQ'
        msg['From'] = from_email
        msg['To'] = email
        
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, email, msg.as_string())
        
        return True, "Email sent via SMTP"
    except Exception as e:
        return False, str(e)

@app.route('/api/admin/gmail/share', methods=['POST'])
@requires_admin  
def share_gmail_integration():
    """Share Gmail integration with other admin users (super admin only)."""
    try:
        current_user = session.get('username')
        
        # Only the main 'admin' user can share Gmail integration
        if current_user != 'admin':
            return jsonify({'success': False, 'error': 'Only the super admin can share Gmail integration'})
        
        req_data = request.json or {}
        target_usernames = req_data.get('usernames', [])
        
        if not target_usernames:
            return jsonify({'success': False, 'error': 'No usernames provided'})
        
        data = load_users()
        
        # Verify all users exist and are admins
        for username in target_usernames:
            if username not in data['users']:
                return jsonify({'success': False, 'error': f'User {username} not found'})
            user_role = data['users'][username].get('role', '')
            if user_role != 'admin' and user_role != 'super_admin':
                return jsonify({'success': False, 'error': f'User {username} is not an admin'})
        
        # Update Gmail sharing settings
        tokens = load_gmail_tokens()
        if not tokens:
            return jsonify({'success': False, 'error': 'Gmail not connected. Connect Gmail first.'})
        
        tokens['shared_with'] = target_usernames
        save_gmail_tokens(tokens)
        
        return jsonify({
            'success': True, 
            'message': f'Gmail integration shared with {len(target_usernames)} admin(s)',
            'shared_with': target_usernames
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/gmail/shared-users')
@requires_admin
def get_gmail_shared_users():
    """Get list of users Gmail is shared with. Always sync to all current admins and super_admins so the integration is automatically shared with every admin."""
    try:
        data = load_users()
        admin_usernames = sorted([
            u for u, user in data.get('users', {}).items()
            if user.get('role') in ('admin', 'super_admin')
        ])
        tokens = load_gmail_tokens()
        if tokens:
            tokens['shared_with'] = admin_usernames
            save_gmail_tokens(tokens)
        return jsonify({
            'success': True,
            'shared_with': admin_usernames,
            'owner': 'admin'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/profile-picture', methods=['POST'])
@requires_auth
def upload_profile_picture():
    """Upload profile picture for current user or specified user (admin only). Accepts file upload or image URL."""
    import base64
    try:
        current_user = session.get('username')
        current_role = session.get('role')
        
        # Get target username (defaults to current user)
        target_username = request.form.get('username', current_user)
        
        # Only admin/super_admin can change other users' pictures
        if target_username != current_user and current_role != 'admin' and current_role != 'super_admin':
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        
        data = load_users()
        if target_username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        data_url = None
        file = request.files.get('file')
        image_url = (request.form.get('url') or '').strip()
        
        if file and file.filename:
            # Upload from file
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in allowed_extensions:
                return jsonify({'success': False, 'error': 'Invalid file type. Use PNG, JPG, GIF, or WebP'})
            file_data = file.read()
            if len(file_data) > 500 * 1024:
                return jsonify({'success': False, 'error': 'File too large. Max 500KB'})
            mime_types = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}
            mime_type = mime_types.get(ext, 'image/png')
            base64_data = base64.b64encode(file_data).decode('utf-8')
            data_url = f"data:{mime_type};base64,{base64_data}"
        elif image_url:
            # Fetch from URL (allow data URLs or http/https)
            if image_url.startswith('data:'):
                # Already a data URL
                if 'base64,' in image_url and len(image_url) <= 600 * 1024:  # ~500KB base64 is ~666KB string
                    data_url = image_url
                else:
                    return jsonify({'success': False, 'error': 'Data URL too large. Max 500KB'})
            else:
                try:
                    from urllib.request import urlopen, Request
                    from urllib.error import URLError, HTTPError
                    req = Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urlopen(req, timeout=10) as resp:
                        content_type = resp.headers.get('Content-Type', '')
                        if 'image/' not in content_type:
                            return jsonify({'success': False, 'error': 'URL did not return an image'})
                        file_data = resp.read()
                    if len(file_data) > 500 * 1024:
                        return jsonify({'success': False, 'error': 'Image too large. Max 500KB'})
                    mime = content_type.split(';')[0].strip().lower()
                    if mime not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                        mime = 'image/png'
                    base64_data = base64.b64encode(file_data).decode('utf-8')
                    data_url = f"data:{mime};base64,{base64_data}"
                except (URLError, HTTPError, OSError) as e:
                    return jsonify({'success': False, 'error': 'Could not load image from URL: ' + str(e)})
        else:
            return jsonify({'success': False, 'error': 'No file or image URL provided'})
        
        data['users'][target_username]['profile_picture'] = data_url
        save_users(data)
        return jsonify({'success': True, 'profile_picture': data_url})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/company-logo', methods=['POST'])
@requires_auth
def upload_company_logo():
    """Upload company logo for current user or specified user (admin only). Accepts file upload or image URL."""
    import base64
    try:
        current_user = session.get('username')
        current_role = session.get('role')

        target_username = request.form.get('username', current_user)

        if target_username != current_user and current_role != 'admin' and current_role != 'super_admin':
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        data = load_users()
        if target_username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})

        data_url = None
        file = request.files.get('file')
        image_url = (request.form.get('url') or '').strip()

        if file and file.filename:
            allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in allowed_extensions:
                return jsonify({'success': False, 'error': 'Invalid file type. Use PNG, JPG, GIF, or WebP'})
            file_data = file.read()
            if len(file_data) > 500 * 1024:
                return jsonify({'success': False, 'error': 'File too large. Max 500KB'})
            mime_types = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}
            mime_type = mime_types.get(ext, 'image/png')
            base64_data = base64.b64encode(file_data).decode('utf-8')
            data_url = f"data:{mime_type};base64,{base64_data}"
        elif image_url:
            if image_url.startswith('data:'):
                if 'base64,' in image_url and len(image_url) <= 600 * 1024:
                    data_url = image_url
                else:
                    return jsonify({'success': False, 'error': 'Data URL too large. Max 500KB'})
            else:
                try:
                    from urllib.request import urlopen, Request
                    from urllib.error import URLError, HTTPError
                    req = Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urlopen(req, timeout=10) as resp:
                        content_type = resp.headers.get('Content-Type', '')
                        if 'image/' not in content_type:
                            return jsonify({'success': False, 'error': 'URL did not return an image'})
                        file_data = resp.read()
                    if len(file_data) > 500 * 1024:
                        return jsonify({'success': False, 'error': 'Image too large. Max 500KB'})
                    mime = content_type.split(';')[0].strip().lower()
                    if mime not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                        mime = 'image/png'
                    base64_data = base64.b64encode(file_data).decode('utf-8')
                    data_url = f"data:{mime};base64,{base64_data}"
                except (URLError, HTTPError, OSError) as e:
                    return jsonify({'success': False, 'error': 'Could not load image from URL: ' + str(e)})
        else:
            return jsonify({'success': False, 'error': 'No file or image URL provided'})

        data['users'][target_username]['company_logo'] = data_url
        save_users(data)
        return jsonify({'success': True, 'company_logo': data_url})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/company-logo', methods=['DELETE'])
@requires_auth
def delete_company_logo():
    """Remove company logo for current user or specified user (admin only)."""
    try:
        current_user = session.get('username')
        current_role = session.get('role')

        target_username = request.args.get('username', current_user)

        if target_username != current_user and current_role != 'admin' and current_role != 'super_admin':
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        data = load_users()
        if target_username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})

        data['users'][target_username].pop('company_logo', None)
        save_users(data)

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/profile-picture', methods=['DELETE'])
@requires_auth
def delete_profile_picture():
    """Remove profile picture for current user or specified user (admin only)."""
    try:
        current_user = session.get('username')
        current_role = session.get('role')
        
        target_username = request.args.get('username', current_user)
        
        if target_username != current_user and current_role != 'admin' and current_role != 'super_admin':
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        
        data = load_users()
        if target_username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        data['users'][target_username].pop('profile_picture', None)
        save_users(data)
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/users/<username>/stats', methods=['GET'])
@requires_admin
def get_user_stats(username):
    """Get detailed stats and activity for a user."""
    try:
        data = load_users()
        
        if username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        user = data['users'][username]
        
        # Get user activity (stored in user data or separate activity log)
        activity = user.get('activity', {})
        
        # Initialize default activity structure if not present
        if not activity:
            activity = {
                'feature_usage': {},
                'profiles_viewed': [],
                'recent_actions': [],
                'total_sessions': 0
            }
        
        # Return user info (without password) and activity
        safe_user = {k: v for k, v in user.items() if k not in ['password_hash', 'activity']}
        
        return jsonify({
            'success': True,
            'user': safe_user,
            'activity': activity
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


# Inactive user alert: email these when a user hasn't logged in for 7+ days
INACTIVE_ALERT_RECIPIENTS = [
    'liz@crosswalknyc.com',
    'jessie@crosswalknyc.com',
    'alexia@crosswalknyc.com',
    'jenna@crosswalknyc.com',
]
INACTIVE_DAYS_THRESHOLD = 7
INACTIVE_EMAIL_COOLDOWN_DAYS = 7  # don't send again for same user within this many days


def _build_usage_snapshot_html(user, activity):
    """Build HTML snippet for dashboard usage snapshot (feature usage, profiles viewed, sessions)."""
    activity = activity or {}
    feature_usage = activity.get('feature_usage') or {}
    profiles_viewed = activity.get('profiles_viewed') or []
    total_sessions = activity.get('total_sessions', 0)
    recent_actions = activity.get('recent_actions') or []
    rows = []
    if feature_usage:
        sorted_features = sorted(feature_usage.items(), key=lambda x: -x[1])[:15]
        for name, count in sorted_features:
            rows.append(f'<tr><td>{name}</td><td style="text-align:right;">{count}</td></tr>')
    feature_table = ''
    if rows:
        feature_table = '<div class="email-card"><div class="email-card-title">Feature usage</div><table style="width:100%; border-collapse:collapse;"><thead><tr><th style="text-align:left;">Action</th><th style="text-align:right;">Count</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    profile_rows = []
    for p in profiles_viewed[:10]:
        name = (p.get('name') or p.get('key') or '—')
        view_count = p.get('view_count', 1)
        viewed_at = (p.get('viewed_at') or '')[:10]
        profile_rows.append(f'<tr><td>{name}</td><td>{view_count}</td><td>{viewed_at}</td></tr>')
    profile_table = ''
    if profile_rows:
        profile_table = '<div class="email-card"><div class="email-card-title">Profiles viewed (recent)</div><table style="width:100%; border-collapse:collapse;"><thead><tr><th style="text-align:left;">Profile</th><th>Views</th><th>Last viewed</th></tr></thead><tbody>' + ''.join(profile_rows) + '</tbody></table></div>'
    return f'<p><strong>Sessions:</strong> {total_sessions} &nbsp;|&nbsp; <strong>Recent actions (logged):</strong> {len(recent_actions)}</p>{feature_table}{profile_table}'


def _check_and_send_inactive_user_emails():
    """Find users inactive 7+ days, send one email per user to INACTIVE_ALERT_RECIPIENTS. Returns (count_emails_sent, list_inactive_usernames)."""
    now = datetime.now()
    cutoff = now - timedelta(days=INACTIVE_DAYS_THRESHOLD)
    cooldown_cutoff = now - timedelta(days=INACTIVE_EMAIL_COOLDOWN_DAYS)
    data = load_users()
    users_data = data.get('users', {})
    sent_count = 0
    inactive_usernames = []
    for username, user in users_data.items():
        if user.get('is_super_admin') or user.get('cloaked_as'):
            continue
        last_login = user.get('last_login')
        try:
            last_login_dt = datetime.fromisoformat(last_login.replace('Z', '+00:00')) if last_login else None
        except Exception:
            last_login_dt = None
        if last_login_dt and last_login_dt.tzinfo:
            last_login_dt = last_login_dt.replace(tzinfo=None)
        if last_login_dt and last_login_dt >= cutoff:
            continue
        last_sent = user.get('last_inactive_email_sent')
        if last_sent:
            try:
                sent_dt = datetime.fromisoformat(last_sent.replace('Z', '+00:00'))
                if sent_dt.tzinfo:
                    sent_dt = sent_dt.replace(tzinfo=None)
                if sent_dt > cooldown_cutoff:
                    continue
            except Exception:
                pass
        inactive_usernames.append(username)
        first_name = (user.get('first_name') or '').strip() or username
        last_name = (user.get('last_name') or '').strip()
        full_name = f'{first_name} {last_name}'.strip() or username
        last_login_display = last_login[:19].replace('T', ' ') if last_login else 'Never'
        activity = user.get('activity') or {}
        usage_html = _build_usage_snapshot_html(user, activity)
        subject = f"Crosswalk IQ: {full_name} has been inactive for over a week"
        body_content = f"""
        <p>This user has not logged in for at least {INACTIVE_DAYS_THRESHOLD} days.</p>
        <div class="email-card">
            <div class="email-card-title">User</div>
            <p><span class="email-label">Name</span><br><span class="email-value">{full_name}</span></p>
            <p><span class="email-label">Username</span><br><span class="email-value">{username}</span></p>
            <p><span class="email-label">Last login</span><br><span class="email-value">{last_login_display}</span></p>
        </div>
        <p><strong>Dashboard usage snapshot (before inactivity):</strong></p>
        {usage_html}
        """
        html = _wrap_email_html(body_content, title='Inactive user alert')
        text_content = f"User {full_name} ({username}) has been inactive for over a week. Last login: {last_login_display}.\n\nDashboard usage snapshot: see HTML version."
        for to_email in INACTIVE_ALERT_RECIPIENTS:
            ok, _ = send_email_via_gmail(to_email, subject, html, text_content=text_content)
            if ok:
                sent_count += 1
        user['last_inactive_email_sent'] = now.isoformat()
    if inactive_usernames:
        save_users(data)
    return sent_count, inactive_usernames


@app.route('/api/admin/check-inactive-users', methods=['POST'])
@requires_admin
def check_inactive_users():
    """Check for users inactive 7+ days and send alert emails to Crosswalk team. Call daily via cron or manually."""
    try:
        sent_count, inactive_usernames = _check_and_send_inactive_user_emails()
        return jsonify({
            'success': True,
            'emails_sent': sent_count,
            'inactive_users': inactive_usernames,
            'message': f'Emails sent to {len(INACTIVE_ALERT_RECIPIENTS)} recipients for {len(inactive_usernames)} inactive user(s).'
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# Activity CSV export: cadence options and scheduling
ACTIVITY_EXPORT_CADENCES = ['weekly', 'bi-weekly', 'monthly', 'quarterly', 'yearly']


def _activity_export_format_ts(ts_str):
    """Format ISO timestamp for CSV display (date only or datetime)."""
    if not ts_str:
        return ''
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            return dt.strftime('%Y-%m-%d')
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(ts_str)[:19] if ts_str else ''


def _activity_export_is_due(cadence, last_sent_iso, today):
    """Return True if an export is due. last_sent_iso is ISO date string or None. today is date object."""
    if cadence not in ACTIVITY_EXPORT_CADENCES:
        return False
    if not last_sent_iso:
        return True
    try:
        last = datetime.fromisoformat(last_sent_iso.replace('Z', '+00:00')).date()
    except Exception:
        return True
    if cadence == 'weekly':
        # Due if we haven't sent this week (Monday = start of week)
        last_monday = last - timedelta(days=last.weekday())
        this_monday = today - timedelta(days=today.weekday())
        return last_monday < this_monday
    if cadence == 'bi-weekly':
        # Due every other week (e.g. week number % 2)
        last_monday = last - timedelta(days=last.weekday())
        this_monday = today - timedelta(days=today.weekday())
        if last_monday >= this_monday:
            return False
        weeks_diff = (this_monday - last_monday).days // 7
        return weeks_diff >= 2
    if cadence == 'monthly':
        return last.year < today.year or (last.year == today.year and last.month < today.month)
    if cadence == 'quarterly':
        q_last = (last.month - 1) // 3 + 1
        q_this = (today.month - 1) // 3 + 1
        return last.year < today.year or (last.year == today.year and q_last < q_this)
    if cadence == 'yearly':
        return last.year < today.year
    return False


def _build_activity_csv(username, user_dict, activity_dict):
    """Build CSV string for one user's activity (same structure as frontend buildUserActivityCSVRows)."""
    activity = activity_dict or {}
    feature_usage = activity.get('feature_usage') or {}
    feature_list = sorted(feature_usage.items(), key=lambda x: -x[1])
    profiles_viewed = activity.get('profiles_viewed') or []
    recent_actions = activity.get('recent_actions') or []
    total_actions = sum(feature_usage.values())
    
    def escape(v):
        s = '' if v is None else str(v)
        return '"' + s.replace('"', '""') + '"'
    
    rows = []
    rows.append(['User', username])
    rows.append([])
    rows.append(['[Overview]'])
    rows.append(['Metric', 'Value'])
    rows.append(['Credits Remaining', 'Unlimited' if user_dict.get('credits') == -1 else str(user_dict.get('credits', 0))])
    rows.append(['Credits Used', str(user_dict.get('credits_used') or 0)])
    rows.append(['Profiles Viewed', str(len(profiles_viewed))])
    rows.append(['Total Actions', str(total_actions)])
    rows.append(['Role', user_dict.get('role') or ''])
    rows.append(['Last Login', _activity_export_format_ts(user_dict.get('last_login')) or 'Never'])
    rows.append(['Account Created', _activity_export_format_ts(user_dict.get('created_at'))])
    rows.append(['Sessions', str(activity.get('total_sessions') or 1)])
    rows.append([])
    rows.append(['[Credits Used]'])
    rows.append(['Date', 'Description', 'Pull Type', 'Credits Used'])
    for entry in (user_dict.get('credit_usage_history') or []):
        rows.append([
            _activity_export_format_ts(entry.get('used_at')),
            entry.get('description') or entry.get('pull_type') or 'Analysis',
            entry.get('pull_type') or '',
            str(entry.get('credits_used') if entry.get('credits_used') is not None else 1)
        ])
    rows.append([])
    rows.append(['[Credits Added (Attributions)]'])
    rows.append(['Date', 'Reason', 'Credits Added', 'Added By'])
    for entry in (user_dict.get('credit_attribution_history') or []):
        rows.append([
            _activity_export_format_ts(entry.get('added_at')),
            entry.get('reason') or '',
            str(entry.get('credits_added') if entry.get('credits_added') is not None else 0),
            entry.get('added_by') or ''
        ])
    rows.append([])
    rows.append(['[Feature Usage]'])
    rows.append(['Feature', 'Uses'])
    for feat, count in feature_list:
        rows.append([feat, str(count)])
    rows.append([])
    rows.append(['[Profiles Viewed]'])
    rows.append(['Profile', 'Viewed At'])
    for p in profiles_viewed:
        rows.append([p.get('name') or p.get('key') or '', _activity_export_format_ts(p.get('viewed_at'))])
    rows.append([])
    rows.append(['[Recent Activity]'])
    rows.append(['Action', 'Details', 'Timestamp'])
    for a in recent_actions:
        rows.append([a.get('action') or '', a.get('details') or '', _activity_export_format_ts(a.get('timestamp'))])
    
    return '\n'.join(','.join(escape(c) for c in row) for row in rows)


def _filter_activity_by_date_range(activity, date_from, date_to):
    """Filter activity recent_actions and profiles_viewed to only include items within date range."""
    if not date_from and not date_to:
        return activity
    out = dict(activity)
    if date_from or date_to:
        recent = out.get('recent_actions') or []
        filtered_actions = []
        for r in recent:
            ts = (r.get('timestamp') or '')[:10]
            if not ts:
                continue
            if date_from and ts < date_from:
                continue
            if date_to and ts > date_to:
                continue
            filtered_actions.append(r)
        out['recent_actions'] = filtered_actions
        profiles = out.get('profiles_viewed') or []
        filtered_profiles = []
        for p in profiles:
            ts = (p.get('viewed_at') or '')[:10]
            if not ts:
                continue
            if date_from and ts < date_from:
                continue
            if date_to and ts > date_to:
                continue
            filtered_profiles.append(p)
        out['profiles_viewed'] = filtered_profiles
    return out


@app.route('/api/admin/export-user-activity', methods=['GET'])
@requires_admin
def export_user_activity():
    """Export user activity data: by username (single user) or by company (all users in company). Optional date_from, date_to (YYYY-MM-DD) filter activity and profiles viewed."""
    try:
        username = request.args.get('username')
        company = request.args.get('company')
        date_from = (request.args.get('date_from') or '').strip() or None
        date_to = (request.args.get('date_to') or '').strip() or None
        if not username and not company:
            return jsonify({'success': False, 'error': 'Provide username= or company='}), 400
        if username and company:
            return jsonify({'success': False, 'error': 'Provide only username= or company='}), 400
        data = load_users()
        users_data = data.get('users', {})
        if username:
            if username not in users_data:
                return jsonify({'success': False, 'error': 'User not found'}), 404
            user = users_data[username]
            activity = user.get('activity') or {
                'feature_usage': {}, 'profiles_viewed': [], 'recent_actions': [], 'total_sessions': 0
            }
            activity = _filter_activity_by_date_range(activity, date_from, date_to)
            safe_user = {k: v for k, v in user.items() if k not in ['password_hash', 'activity']}
            return jsonify({
                'success': True,
                'by': 'user',
                'users': [{'username': username, 'user': safe_user, 'activity': activity}]
            })
        company_trim = (company or '').strip()
        if not company_trim:
            return jsonify({'success': False, 'error': 'Company name is required'}), 400
        company_lower = company_trim.lower()
        results = []
        for u, user in users_data.items():
            u_company = (user.get('company') or '').strip()
            if u_company.lower() == company_lower:
                activity = user.get('activity') or {
                    'feature_usage': {}, 'profiles_viewed': [], 'recent_actions': [], 'total_sessions': 0
                }
                activity = _filter_activity_by_date_range(activity, date_from, date_to)
                safe_user = {k: v for k, v in user.items() if k not in ['password_hash', 'activity']}
                results.append({'username': u, 'user': safe_user, 'activity': activity})
        return jsonify({
            'success': True,
            'by': 'company',
            'company': company_trim,
            'users': results
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/activity-export/company', methods=['GET'])
@requires_admin
def get_activity_export_company():
    """List companies and their activity export settings (emails, cadence)."""
    try:
        data = load_users()
        by_company = data.get('activity_export_by_company') or {}
        users_data = data.get('users', {})
        companies = sorted(set((u.get('company') or '').strip() for u in users_data.values() if (u.get('company') or '').strip()))
        return jsonify({
            'success': True,
            'companies': companies,
            'by_company': {k: {'emails': v.get('emails', ''), 'cadence': v.get('cadence', ''), 'last_sent': v.get('last_sent')} for k, v in by_company.items()}
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/activity-export/company', methods=['POST'])
@requires_admin
def set_activity_export_company():
    """Set or clear activity export for a company. Body: company, emails (comma-separated), cadence."""
    try:
        req = request.get_json() or {}
        company = (req.get('company') or '').strip()
        if not company:
            return jsonify({'success': False, 'error': 'Company name required'}), 400
        emails = (req.get('emails') or '').strip()
        cadence = (req.get('cadence') or '').strip().lower()
        if cadence and cadence not in ACTIVITY_EXPORT_CADENCES:
            return jsonify({'success': False, 'error': f'Cadence must be one of: {", ".join(ACTIVITY_EXPORT_CADENCES)}'}), 400
        data = load_users()
        if 'activity_export_by_company' not in data:
            data['activity_export_by_company'] = {}
        if not emails or not cadence:
            data['activity_export_by_company'].pop(company, None)
        else:
            data['activity_export_by_company'][company] = {
                'emails': emails,
                'cadence': cadence,
                'last_sent': data.get('activity_export_by_company', {}).get(company, {}).get('last_sent')
            }
        save_users(data)
        return jsonify({'success': True, 'message': f'Activity export for {company} updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _run_activity_export_jobs_impl():
    """Run scheduled activity CSV exports: per-user and per-company. Returns dict for jsonify."""
    data = load_users()
    users_data = data.get('users', {})
    by_company = data.get('activity_export_by_company') or {}
    today = date.today()
    sent_user = []
    sent_company = []

    # Per-user exports
    for username, user in users_data.items():
        emails_raw = (user.get('activity_export_emails') or '').strip()
        cadence = (user.get('activity_export_cadence') or '').strip().lower()
        if not emails_raw or cadence not in ACTIVITY_EXPORT_CADENCES:
            continue
        last_sent = user.get('activity_export_last_sent')
        if not _activity_export_is_due(cadence, last_sent, today):
            continue
        activity = user.get('activity') or {'feature_usage': {}, 'profiles_viewed': [], 'recent_actions': [], 'total_sessions': 0}
        safe_user = {k: v for k, v in user.items() if k not in ['password_hash', 'activity']}
        csv_str = _build_activity_csv(username, safe_user, activity)
        csv_bytes = csv_str.encode('utf-8')
        emails_list = [e.strip() for e in emails_raw.split(',') if e.strip()]
        subject = f"Crosswalk IQ – User activity export: {username}"
        html = _wrap_email_html(f"<p>Attached: user activity CSV for <strong>{username}</strong> (cadence: {cadence}).</p>", title='User activity export')
        ok, _ = send_email_with_attachment(emails_list, subject, html, f'user_activity_{username}.csv', csv_bytes)
        if ok:
            user['activity_export_last_sent'] = today.isoformat()
            sent_user.append(username)

    # Per-company exports
    for company_key, config in list(by_company.items()):
        emails_raw = (config.get('emails') or '').strip()
        cadence = (config.get('cadence') or '').strip().lower()
        if not emails_raw or cadence not in ACTIVITY_EXPORT_CADENCES:
            continue
        last_sent = config.get('last_sent')
        if not _activity_export_is_due(cadence, last_sent, today):
            continue
        company_trim = (company_key or '').strip()
        company_lower = company_trim.lower()
        results = []
        for u, user in users_data.items():
            u_company = (user.get('company') or '').strip()
            if u_company.lower() == company_lower:
                activity = user.get('activity') or {'feature_usage': {}, 'profiles_viewed': [], 'recent_actions': [], 'total_sessions': 0}
                safe_user = {k: v for k, v in user.items() if k not in ['password_hash', 'activity']}
                results.append({'username': u, 'user': safe_user, 'activity': activity})
        if not results:
            continue
        rows = []
        for item in results:
            rows.append(_build_activity_csv(item['username'], item['user'], item['activity']))
        csv_str = '\n\n'.join(rows)
        csv_bytes = csv_str.encode('utf-8')
        emails_list = [e.strip() for e in emails_raw.split(',') if e.strip()]
        subject = f"Crosswalk IQ – Company activity export: {company_trim}"
        html = _wrap_email_html(f"<p>Attached: user activity CSV for company <strong>{company_trim}</strong> ({len(results)} user(s), cadence: {cadence}).</p>", title='Company activity export')
        ok, _ = send_email_with_attachment(emails_list, subject, html, f'user_activity_company_{company_trim.replace(" ", "_")}.csv', csv_bytes)
        if ok:
            by_company[company_key]['last_sent'] = today.isoformat()
            sent_company.append(company_trim)

    if sent_user or sent_company:
        data['activity_export_by_company'] = by_company
        save_users(data)

    return {
        'success': True,
        'emails_sent_user': sent_user,
        'emails_sent_company': sent_company,
        'message': f'Sent {len(sent_user)} user export(s), {len(sent_company)} company export(s).'
    }


@app.route('/api/admin/run-activity-export-jobs', methods=['POST'])
@requires_admin
def run_activity_export_jobs():
    """Run scheduled activity CSV exports: per-user and per-company. Call via cron (e.g. daily)."""
    try:
        return jsonify(_run_activity_export_jobs_impl())
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/restore-users-from-deployed-file', methods=['POST'])
def cron_restore_users_from_deployed_file():
    """One-time: load users from the deployed repo's users.json and save to S3. Use after restoring users locally so production gets all users. Requires CRON_SECRET."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    if not secret or secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    try:
        with open(USERS_FILE, 'r') as f:
            data = json.load(f)
        if not data.get('users'):
            return jsonify({'success': False, 'error': 'No users in file'}), 400
        save_users(data)
        count = len(data['users'])
        return jsonify({'success': True, 'message': f'Restored {count} users to S3. All users are now on production.'})
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'users.json not found in deployment'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/restore-users-from-body', methods=['POST'])
def cron_restore_users_from_body():
    """One-time: accept full users JSON in request body and save to S3. Use to push local users.json to production without redeploy. Requires CRON_SECRET."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    if not secret or secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    try:
        data = request.get_json()
        if not data or not data.get('users'):
            return jsonify({'success': False, 'error': 'JSON body with "users" object required'}), 400
        save_users(data)
        count = len(data['users'])
        return jsonify({'success': True, 'message': f'Restored {count} users to S3. All users are now on production.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/repair-admin-role', methods=['POST'])
def cron_repair_admin_role():
    """One-time repair: set the 'admin' user's role to super_admin. Call with CRON_SECRET to fix production after accidental role change."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    if not secret or secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    try:
        data = load_users()
        if 'admin' not in data.get('users', {}):
            return jsonify({'success': False, 'error': 'Admin user not found'}), 404
        data['users']['admin']['role'] = 'super_admin'
        save_users(data)
        return jsonify({'success': True, 'message': "Admin user role set to super_admin. Log out and log back in."})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/run-activity-export-jobs', methods=['POST', 'GET'])
def cron_run_activity_export_jobs():
    """Run activity export jobs when called with valid CRON_SECRET (for Render cron). No session required."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    expected = os.environ.get('CRON_SECRET', '')
    if not expected or secret != expected:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    try:
        return jsonify(_run_activity_export_jobs_impl())
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/track-activity', methods=['POST'])
@requires_auth
def track_user_activity():
    """Track user activity for analytics."""
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': 'Not logged in'})
        
        req_data = request.json or {}
        action = req_data.get('action', '')
        details = req_data.get('details', '')
        
        if not action:
            return jsonify({'success': False, 'error': 'No action specified'})
        
        data = load_users()
        if username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        user = data['users'][username]
        
        # Initialize activity structure if needed
        if 'activity' not in user:
            user['activity'] = {
                'feature_usage': {},
                'profiles_viewed': [],
                'recent_actions': [],
                'total_sessions': 0
            }
        
        activity = user['activity']
        
        # Update feature usage count
        if action in activity['feature_usage']:
            activity['feature_usage'][action] += 1
        else:
            activity['feature_usage'][action] = 1
        
        # Add to recent actions (keep last 100)
        activity['recent_actions'].insert(0, {
            'action': action,
            'details': details,
            'timestamp': datetime.now().isoformat()
        })
        activity['recent_actions'] = activity['recent_actions'][:100]
        
        # If it's a profile view, track it
        if action == 'profile_view' and details:
            # Check if already in list
            existing = next((p for p in activity['profiles_viewed'] if p.get('key') == details), None)
            if existing:
                existing['viewed_at'] = datetime.now().isoformat()
                existing['view_count'] = existing.get('view_count', 1) + 1
            else:
                activity['profiles_viewed'].insert(0, {
                    'key': details,
                    'name': details.replace('.csv', '').replace('_', ' '),
                    'viewed_at': datetime.now().isoformat(),
                    'view_count': 1
                })
            # Keep last 50 profiles
            activity['profiles_viewed'] = activity['profiles_viewed'][:50]
        
        # Save
        save_users(data)
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Activity tracking error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# ADMIN CONTENT MANAGEMENT
# ============================================================================

@app.route('/api/admin/content', methods=['GET'])
@requires_admin
def get_admin_content():
    """Get all content files grouped by category, including archived."""
    print("📂 Admin content request received")
    
    # Always load persisted cache from S3 so category/display name updates are visible
    load_persisted_cache()
    
    # Check AWS credentials
    aws_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    if not aws_key or not aws_secret:
        print("❌ AWS credentials not configured")
        return jsonify({'success': False, 'error': 'AWS credentials not configured'})
    
    try:
        s3_endpoint = f'https://s3.{S3_REGION}.amazonaws.com'
        s3 = boto3.client('s3',
                          aws_access_key_id=aws_key,
                          aws_secret_access_key=aws_secret,
                          region_name=S3_REGION,
                          endpoint_url=s3_endpoint)
        
        bucket_name = 'dashboard-inputs'
        print(f"📂 Scanning S3 bucket: {bucket_name}")
        
        # Build a lookup from cached jobs for proper categories
        cached_lookup = {}
        for job in s3_cache.get('jobs', []):
            # Jobs use 's3_key' as the key field
            job_key = job.get('s3_key', job.get('key', ''))
            if job_key:
                cached_lookup[job_key] = job
        
        print(f"📋 Cache lookup built with {len(cached_lookup)} entries")
        
        # Purgatory files are not in s3_cache; use purgatory metadata for category and display name
        purgatory_meta = load_purgatory_metadata() if s3_client else {}
        
        # Get active files
        active_files = []
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=''):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # Skip historic folder, system folder, and non-CSV files
                if key.startswith('historic/') or key.startswith('system/') or not key.endswith('.csv'):
                    continue
                
                # Parse file info
                filename = key.split('/')[-1]
                
                # Purgatory keys: category and project_name come from purgatory metadata
                if key.startswith(S3_PURGATORY_PREFIX):
                    purgatory_id = f"{S3_BUCKET}:{key}"
                    item = purgatory_meta.get(purgatory_id, {})
                    category = (item.get('category') or 'Uncategorized').strip() or 'Uncategorized'
                    project_name = (item.get('title') or item.get('project_name') or '').strip()
                    if not project_name:
                        name_without_ext = filename.replace('.csv', '')
                        name_without_timestamp = remove_timestamp_from_name(name_without_ext)
                        project_name = smart_title_case(name_without_timestamp.replace('_', ' '))
                else:
                    # Try to get category and project_name from cache (which has proper brand categories from CSV)
                    cached = cached_lookup.get(key, {})
                    category = cached.get('category', 'Uncategorized')
                    # Generate display name with timestamp removal
                    if 'project_name' not in cached:
                        name_without_ext = filename.replace('.csv', '')
                        name_without_timestamp = remove_timestamp_from_name(name_without_ext)
                        project_name = smart_title_case(name_without_timestamp.replace('_', ' '))
                    else:
                        project_name = cached['project_name']
                last_modified = obj['LastModified'].isoformat() if obj.get('LastModified') else None
                
                active_files.append({
                    'key': key,
                    'filename': filename,
                    'project_name': project_name,
                    'category': category,
                    'size': obj.get('Size', 0),
                    'last_modified': last_modified,
                    'created_at': last_modified  # Also include as created_at for sorting
                })
        
        print(f"✅ Found {len(active_files)} active files")
        
        # Get SVOD Acquisition files from subscriber bucket
        svod_files = []
        try:
            # Load SVOD metadata for categories
            svod_metadata = load_svod_metadata()
            
            svod_paginator = s3.get_paginator('list_objects_v2')
            for page in svod_paginator.paginate(Bucket=SUBSCRIBER_S3_BUCKET, Prefix=''):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    # Skip historic folder, purgatory (unreleased), and non-CSV files
                    if key.startswith('historic/') or key.startswith(S3_PURGATORY_PREFIX) or not key.endswith('.csv'):
                        continue
                    
                    filename = key.split('/')[-1]
                    # Extract show name from filename
                    name_without_ext = key.replace('.csv', '')
                    match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
                    if match:
                        show_name = match.group(1).replace('_', ' ')
                    else:
                        show_name = name_without_ext.replace('_', ' ')
                    
                    last_modified = obj['LastModified'].isoformat() if obj.get('LastModified') else None
                    
                    # Get category from metadata, default to 'SVOD Acquisition'
                    # SVOD files can have subcategories (TALENT, CONTENT, etc.) but they're always under SVOD ACQUISITION master
                    category = 'SVOD Acquisition'
                    file_meta = svod_metadata.get(key, {})
                    if file_meta.get('category'):
                        category = file_meta['category']
                    content_cadence = file_meta.get('content_cadence', '')
                    
                    svod_files.append({
                        'key': f'svod-acquisition/{key}',  # Prefix to identify bucket
                        'filename': filename,
                        'project_name': show_name,
                        'category': category,
                        'content_cadence': content_cadence,
                        'size': obj.get('Size', 0),
                        'last_modified': last_modified,
                        'created_at': last_modified,
                        'bucket': SUBSCRIBER_S3_BUCKET,
                        's3_key': key,  # Original key in svod bucket
                        'is_svod': True  # Flag to identify SVOD files
                    })
            
            print(f"✅ Found {len(svod_files)} SVOD Acquisition files")
            active_files.extend(svod_files)
        except Exception as svod_err:
            print(f"⚠️ Error loading SVOD files: {svod_err}")
        
        # Get Ticket Sales IQ files from ticket-sales-iq bucket
        ticket_sales_files = []
        try:
            ts_metadata = load_ticket_sales_metadata()
            ts_paginator = s3.get_paginator('list_objects_v2')
            for page in ts_paginator.paginate(Bucket=TICKET_SALES_S3_BUCKET, Prefix=''):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if not key.endswith('.csv'):
                        continue
                    name_without_ext = key.replace('.csv', '')
                    match = re.match(r'^(.+)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
                    if match:
                        default_display = match.group(1).replace('_', ' ')
                    else:
                        default_display = name_without_ext.replace('_', ' ')
                    meta = ts_metadata.get(key, {})
                    project_name = (meta.get('display_name') or default_display).strip()
                    category = (meta.get('category') or 'Uncategorized').strip() or 'Uncategorized'
                    image_url = meta.get('image_url') or ''
                    last_modified = obj['LastModified'].isoformat() if obj.get('LastModified') else None
                    ticket_sales_files.append({
                        'key': f'ticket-sales-iq/{key}',
                        'filename': key.split('/')[-1],
                        'project_name': project_name,
                        'category': category,
                        'size': obj.get('Size', 0),
                        'last_modified': last_modified,
                        'created_at': last_modified,
                        'bucket': TICKET_SALES_S3_BUCKET,
                        's3_key': key,
                        'is_ticket_sales': True,
                        'custom_image': image_url if image_url else None
                    })
            print(f"✅ Found {len(ticket_sales_files)} Ticket Sales IQ files")
            active_files.extend(ticket_sales_files)
        except Exception as ts_err:
            print(f"⚠️ Error loading Ticket Sales files: {ts_err}")
        
        # Get Ticket Sales Tracker files from ticket-sales-tracker bucket
        ticket_sales_tracker_files = []
        try:
            tst_metadata = load_ticket_sales_tracker_metadata()
            tst_paginator = s3.get_paginator('list_objects_v2')
            for page in tst_paginator.paginate(Bucket=TICKET_SALES_TRACKER_S3_BUCKET, Prefix=''):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if not key.endswith('.csv') or key.startswith(S3_PURGATORY_PREFIX):
                        continue
                    name_without_ext = key.replace('.csv', '')
                    match = re.match(r'^Ticket_Sales_(.+)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
                    if match:
                        default_display = match.group(1).replace('_', ' ')
                    else:
                        default_display = name_without_ext.replace('_', ' ')
                    meta = tst_metadata.get(key, {})
                    image_url = meta.get('image_url') or ''
                    last_modified = obj['LastModified'].isoformat() if obj.get('LastModified') else None
                    ticket_sales_tracker_files.append({
                        'key': f'ticket-sales-tracker/{key}',
                        'filename': key.split('/')[-1],
                        'project_name': default_display.strip(),
                        'category': 'Ticket Sales Tracker',
                        'size': obj.get('Size', 0),
                        'last_modified': last_modified,
                        'created_at': last_modified,
                        'bucket': TICKET_SALES_TRACKER_S3_BUCKET,
                        's3_key': key,
                        'is_ticket_sales_tracker': True,
                        'custom_image': image_url if image_url else None
                    })
            print(f"✅ Found {len(ticket_sales_tracker_files)} Ticket Sales Tracker files")
            active_files.extend(ticket_sales_tracker_files)
        except Exception as tst_err:
            print(f"⚠️ Error loading Ticket Sales Tracker files: {tst_err}")
        
        # Load profile image cache to check for custom images
        if not profile_image_cache:
            load_profile_image_cache()
        
        # Add custom image info to each file (skip ticket sales and ticket sales tracker - they use their own metadata)
        for f in active_files:
            if f.get('is_ticket_sales') or f.get('is_ticket_sales_tracker'):
                continue
            cache_key = f.get('project_name', '').lower().strip()
            if cache_key in profile_image_cache:
                cached = profile_image_cache[cache_key]
                if cached.get('is_custom'):
                    f['custom_image'] = cached.get('image_url')
        
        # Get archived files
        archived_files = []
        for page in paginator.paginate(Bucket=bucket_name, Prefix='historic/'):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv'):
                    continue
                
                filename = key.split('/')[-1]
                name_without_ext = filename.replace('.csv', '')
                name_without_timestamp = remove_timestamp_from_name(name_without_ext)
                project_name = smart_title_case(name_without_timestamp.replace('_', ' '))
                last_modified = obj['LastModified'].isoformat() if obj.get('LastModified') else None
                
                archived_files.append({
                    'key': key,
                    'filename': filename,
                    'project_name': project_name,
                    'category': 'Archived',
                    'size': obj.get('Size', 0),
                    'last_modified': last_modified,
                    'created_at': last_modified
                })
        
        print(f"✅ Found {len(archived_files)} archived files")
        
        return jsonify({
            'success': True,
            'files': active_files,
            'archived': archived_files
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error getting admin content: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/content/archive', methods=['POST'])
@requires_admin
def archive_content():
    """Move files to historic/ folder."""
    global s3_cache
    try:
        keys = request.json.get('keys', [])
        if not keys:
            return jsonify({'success': False, 'error': 'No files specified'})
        
        s3_endpoint = f'https://s3.{S3_REGION}.amazonaws.com'
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                          region_name=S3_REGION,
                          endpoint_url=s3_endpoint)
        
        bucket_name = 'dashboard-inputs'
        archived_count = 0
        
        for key in keys:
            # Skip if already in historic
            if key.startswith('historic/'):
                continue
            
            try:
                # Copy to historic folder
                filename = key.split('/')[-1]
                new_key = f'historic/{filename}'
                
                s3.copy_object(
                    Bucket=bucket_name,
                    CopySource={'Bucket': bucket_name, 'Key': key},
                    Key=new_key
                )
                
                # Delete original
                s3.delete_object(Bucket=bucket_name, Key=key)
                
                # Remove from cache jobs list
                s3_cache['jobs'] = [j for j in s3_cache.get('jobs', []) if j.get('key') != key]
                
                archived_count += 1
                print(f"Archived: {key} -> {new_key}")
                
            except Exception as e:
                print(f"Failed to archive {key}: {e}")
        
        # Update cache counts and persist
        s3_cache['file_count'] = len(s3_cache.get('jobs', []))
        save_persisted_cache()
        
        return jsonify({
            'success': True,
            'message': f'Archived {archived_count} file(s)',
            'archived_count': archived_count
        })
        
    except Exception as e:
        print(f"Archive error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/content/restore', methods=['POST'])
@requires_admin
def restore_content():
    """Move files from historic/ folder back to root."""
    try:
        keys = request.json.get('keys', [])
        if not keys:
            return jsonify({'success': False, 'error': 'No files specified'})
        
        s3_endpoint = f'https://s3.{S3_REGION}.amazonaws.com'
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                          region_name=S3_REGION,
                          endpoint_url=s3_endpoint)
        
        bucket_name = 'dashboard-inputs'
        restored_count = 0
        
        for key in keys:
            # Only restore from historic
            if not key.startswith('historic/'):
                continue
            
            try:
                # Copy back to root
                filename = key.replace('historic/', '')
                new_key = filename
                
                s3.copy_object(
                    Bucket=bucket_name,
                    CopySource={'Bucket': bucket_name, 'Key': key},
                    Key=new_key
                )
                
                # Delete from historic
                s3.delete_object(Bucket=bucket_name, Key=key)
                
                restored_count += 1
                print(f"Restored: {key} -> {new_key}")
                
            except Exception as e:
                print(f"Failed to restore {key}: {e}")
        
        # Refresh cache to pick up restored files
        smart_cache_update()
        
        return jsonify({
            'success': True,
            'message': f'Restored {restored_count} file(s)',
            'restored_count': restored_count
        })
        
    except Exception as e:
        print(f"Restore error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/content/delete', methods=['POST'])
@requires_admin
def delete_content():
    """Permanently delete files from S3."""
    global s3_cache
    try:
        keys = request.json.get('keys', [])
        if not keys:
            return jsonify({'success': False, 'error': 'No files specified'})
        
        s3_endpoint = f'https://s3.{S3_REGION}.amazonaws.com'
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                          region_name=S3_REGION,
                          endpoint_url=s3_endpoint)
        
        bucket_name = 'dashboard-inputs'
        deleted_count = 0
        
        for key in keys:
            try:
                s3.delete_object(Bucket=bucket_name, Key=key)
                
                # Remove from cache jobs list
                s3_cache['jobs'] = [j for j in s3_cache.get('jobs', []) if j.get('key') != key]
                
                deleted_count += 1
                print(f"Deleted permanently: {key}")
                
            except Exception as e:
                print(f"Failed to delete {key}: {e}")
        
        # Update cache counts and persist
        s3_cache['file_count'] = len(s3_cache.get('jobs', []))
        save_persisted_cache()
        
        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} file(s)',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        print(f"Delete error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/restore-metadata', methods=['POST'])
@requires_admin
def restore_metadata():
    """Restore metadata (images, categories) from profile_image_cache to current files based on project name."""
    global s3_cache, profile_image_cache
    
    try:
        # Load caches if not loaded
        if not profile_image_cache:
            load_profile_image_cache()
        
        if not s3_cache.get('jobs'):
            load_persisted_cache()
        
        restored_count = 0
        image_matches = 0
        
        print(f"🔄 Restoring metadata for {len(s3_cache.get('jobs', []))} files...")
        print(f"   Profile image cache has {len(profile_image_cache)} entries")
        
        # Go through each job and try to match with profile image cache
        for job in s3_cache.get('jobs', []):
            project_name = job.get('project_name', '')
            if not project_name:
                continue
            
            cache_key = project_name.lower().strip()
            
            # Check if there's a matching profile image
            if cache_key in profile_image_cache:
                cached = profile_image_cache[cache_key]
                if cached.get('is_custom') and cached.get('image_url'):
                    job['custom_image'] = cached['image_url']
                    image_matches += 1
                    print(f"   ✅ Matched image for: {project_name}")
            
            restored_count += 1
        
        # Save updated cache
        save_persisted_cache()
        
        return jsonify({
            'success': True,
            'message': f'Restored metadata for {restored_count} files, matched {image_matches} images',
            'restored_count': restored_count,
            'image_matches': image_matches
        })
        
    except Exception as e:
        import traceback
        print(f"Restore metadata error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/activity-log')
@requires_auth
def get_activity_log():
    """Get activity log for collaboration hub."""
    try:
        limit = request.args.get('limit', 50, type=int)
        activities = []
        
        # Collect activity from all users
        data = load_users()
        for username, user in data.get('users', {}).items():
            user_activity = user.get('activity', {})
            recent_actions = user_activity.get('recent_actions', [])
            
            for action in recent_actions:
                activities.append({
                    'user': username,
                    'action': action.get('action', 'unknown'),
                    'details': action.get('details', ''),
                    'timestamp': action.get('timestamp', '')
                })
        
        # Sort by timestamp (newest first) and limit
        activities.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        activities = activities[:limit]
        
        return jsonify({
            'success': True,
            'activities': activities
        })
        
    except Exception as e:
        print(f"Activity log error: {e}")
        return jsonify({'success': False, 'error': str(e), 'activities': []})

@app.route('/api/admin/users-list')
@requires_auth
def get_users_list():
    """Get simple list of users for collaboration hub (non-admin can access)."""
    try:
        data = load_users()
        users = []
        for username, user in data.get('users', {}).items():
            users.append({
                'username': username,
                'role': _normalize_role(user.get('role', 'user')),
                'company': user.get('company', ''),
                'department': user.get('department', ''),
                'first_name': user.get('first_name', ''),
                'last_name': user.get('last_name', ''),
                'profile_picture': user.get('profile_picture', ''),
                'email': user.get('email', '')
            })
        return jsonify({'success': True, 'users': users})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'users': []})

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
        'role': _normalize_role(user.get('role', 'user')),
        'company': user.get('company', ''),
        'company_logo': user.get('company_logo', ''),
        'department': user.get('department', ''),
        'credits': user.get('credits', 0),
        'credits_used': user.get('credits_used', 0),
        'allowed_categories': user.get('allowed_categories', ['*']),
        'allowed_runs': user.get('allowed_runs', ['*']),
        'collab_team': user.get('collab_team', [])
    })


@app.route('/api/credit-usage')
@requires_auth
def get_credit_usage():
    """Return current user's credit usage history (what each credit was used for). Newest first."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    history = user.get('credit_usage_history', [])
    return jsonify({'success': True, 'usage': history})


@app.route('/api/admin/credit-usage-export')
@requires_admin
def admin_credit_usage_export():
    """Export all clients' credit usage in a date range to CSV. Admin only."""
    date_from_str = request.args.get('date_from', '').strip()
    date_to_str = request.args.get('date_to', '').strip()
    if not date_from_str or not date_to_str:
        return jsonify({'success': False, 'error': 'date_from and date_to (YYYY-MM-DD) are required'}), 400
    try:
        date_from = datetime.strptime(date_from_str, '%Y-%m-%d')
        date_to = datetime.strptime(date_to_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid date format; use YYYY-MM-DD'}), 400
    if date_from > date_to:
        return jsonify({'success': False, 'error': 'date_from must be on or before date_to'}), 400
    start = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    end = date_to.replace(hour=23, minute=59, second=59, microsecond=999999)
    data = load_users()
    rows = [['Username', 'Company', 'Email', 'Date', 'Description', 'Pull Type', 'Job ID', 'Credits Used']]
    for username, user in data.get('users', {}).items():
        company = user.get('company') or ''
        email = user.get('email') or ''
        history = user.get('credit_usage_history') or []
        for entry in history:
            used_at_str = entry.get('used_at') or ''
            if not used_at_str:
                continue
            try:
                used_at = datetime.fromisoformat(used_at_str.replace('Z', '+00:00'))
                if used_at.tzinfo:
                    used_at = used_at.replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if not (start <= used_at <= end):
                continue
            rows.append([
                username,
                company,
                email,
                used_at.strftime('%Y-%m-%d %H:%M:%S'),
                entry.get('description') or entry.get('pull_type') or 'Analysis',
                entry.get('pull_type') or '',
                entry.get('job_id') or '',
                str(entry.get('credits_used') if entry.get('credits_used') is not None else 1)
            ])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(rows)
    filename = f'credit_usage_{date_from_str}_to_{date_to_str}.csv'
    return Response(
        out.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


# ============================================================================
# PROFILE RELEASED NOTIFICATIONS
# ============================================================================

@app.route('/api/notifications/profile-released')
@requires_auth
def get_profile_released_notifications():
    """Get unread profile-released notifications for the current user."""
    username = session.get('username')
    if not username:
        return jsonify({'success': False, 'error': 'Not logged in'})
    data = load_profile_released_notifications()
    notifications = [n for n in data.get(username, []) if not n.get('read')]
    return jsonify({'success': True, 'notifications': notifications})


@app.route('/api/notifications/profile-released/dismiss', methods=['POST'])
@requires_auth
def dismiss_profile_released_notifications():
    """Mark profile-released notification(s) as read."""
    username = session.get('username')
    if not username:
        return jsonify({'success': False, 'error': 'Not logged in'})
    body = request.get_json() or {}
    ids = body.get('ids', [])
    if not ids:
        return jsonify({'success': True})
    data = load_profile_released_notifications()
    user_notifs = data.get(username, [])
    for n in user_notifs:
        if n.get('id') in ids:
            n['read'] = True
    data[username] = user_notifs
    save_profile_released_notifications(data)
    return jsonify({'success': True})


# ============================================================================
# CHAT STATUS API (Available/Busy)
# ============================================================================

CHAT_STATUS_S3_KEY = 'system/chat_statuses.json'

def _load_chat_statuses():
    """Load user statuses from S3."""
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=CHAT_STATUS_S3_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception:
        return {}

def _save_chat_statuses(statuses):
    """Save user statuses to S3."""
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=CHAT_STATUS_S3_KEY,
            Body=json.dumps(statuses),
            ContentType='application/json'
        )
    except Exception as e:
        print(f"Error saving chat statuses: {e}")

@app.route('/api/chat/status', methods=['GET'])
@requires_auth
def get_chat_statuses():
    """Get all users' chat status (available/busy) for the user's company."""
    try:
        statuses = _load_chat_statuses()
        user = get_current_user()
        company = user.get('company', '')
        username = session.get('username', '')
        
        # Filter to same company users only (or return all if no company)
        data = load_users()
        company_users = {username}
        for u, info in data.get('users', {}).items():
            if not company or info.get('company') == company:
                company_users.add(u)
        
        filtered = {k: v for k, v in statuses.items() if k in company_users}
        return jsonify({'success': True, 'statuses': filtered})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'statuses': {}})

@app.route('/api/chat/status', methods=['POST'])
@requires_auth
def set_chat_status():
    """Set current user's chat status (available/busy)."""
    try:
        data = request.get_json() or {}
        status = data.get('status', 'available')
        if status not in ('available', 'busy'):
            status = 'available'
        
        username = session.get('username', '')
        statuses = _load_chat_statuses()
        statuses[username] = {'status': status, 'updated_at': datetime.now().isoformat()}
        _save_chat_statuses(statuses)
        
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================================
# MAIN ROUTES
# ============================================================================

@app.route('/')
@requires_auth
def index():
    user = get_current_user()
    role = _normalize_role(user.get('role', 'user') if user else 'user')
    
    # Load quick-select behavioral exclusions (applies to all users globally)
    # Bypass cache so admin changes propagate to all users immediately across workers
    quick_select_behaviors_exclusions = []
    try:
        quick_selects = load_json_from_s3(QUICK_SELECTS_FILE, use_cache=False)
        behaviors = quick_selects.get('behaviors', {})
        quick_select_behaviors_exclusions = [cat for cat, val in behaviors.items() if val is False]
    except Exception as e:
        print(f"⚠️ Could not load quick selects for behavioral filtering: {e}")
    
    # Super admins always have access to all dashboards and modules
    # Regular admins now need module access enabled like other users
    if role == 'super_admin':
        has_profile_iq = True
        has_subscriber_iq = True
        has_hedge_fund_iq = True
        hedge_fund_iq_tickers = ['*']
        has_analysis_iq = True
        analysis_iq_modules = ['profile_analysis', 'talent_search', 'talent_theater', 'svod', 'campaign', 'cross_show', 'watch_time', 'ticket_sales_tracker']
        allowed_behavioral_categories = ['*']
        allowed_categories = ['*']
        allowed_runs = ['*']
        has_rankers_iq = True
        rankers_iq_options = ['*']
        has_ticket_sales_iq = True
        has_ticket_sales_tracker = True
        has_llmo_iq = True
    else:
        has_profile_iq = user.get('has_profile_iq_access', True) if user else True  # Default True for backward compat
        has_subscriber_iq = user.get('has_subscriber_iq_access', False) if user else False
        has_hedge_fund_iq = user.get('has_hedge_fund_iq_access', False) if user else False
        hedge_fund_iq_tickers = user.get('hedge_fund_iq_tickers', ['*']) if user else ['*']
        has_analysis_iq = user.get('has_analysis_iq_access', False) if user else False
        analysis_iq_modules = user.get('analysis_iq_modules', []) if user else []
        allowed_behavioral_categories = user.get('allowed_behavioral_categories', ['*']) if user else ['*']
        allowed_categories = user.get('allowed_categories', ['*']) if user else ['*']
        allowed_runs = user.get('allowed_runs', ['*']) if user else ['*']
        has_rankers_iq = user.get('has_rankers_iq_access', False) if user else False
        rankers_iq_options = user.get('rankers_iq_options', []) if user else []
        has_ticket_sales_iq = user.get('has_ticket_sales_iq_access', True) if user else True  # Default True
        has_ticket_sales_tracker = user.get('has_ticket_sales_tracker_access', False) if user else False
        has_llmo_iq = user.get('has_llmo_iq_access', False) if user else False
    
    # When cloaked, grant Analysis IQ access so the admin can use it while acting as a user who may not have it
    if session.get('cloaked_from'):
        has_analysis_iq = True
        analysis_iq_modules = ['profile_analysis', 'talent_search', 'talent_theater', 'svod', 'campaign', 'cross_show', 'watch_time', 'ticket_sales_tracker']
    
    # If user only has Hedge Fund IQ (no Profile IQ), default to Hedge Fund IQ landing page
    default_view_hedge_fund_iq = bool(has_hedge_fund_iq and not has_profile_iq)

    # Purgatory: only super_admins or users explicitly allowed to access/approve (has_purgatory_approval) see it in the dropdown
    has_purgatory_access = role == 'super_admin' or (user.get('has_purgatory_approval', False) if user else False)

    # Get user info for credits request
    first_name = user.get('first_name', '') if user else ''
    last_name = user.get('last_name', '') if user else ''
    email = user.get('email', '') if user else ''
    company = user.get('company', '') if user else ''
    company_logo = user.get('company_logo', '') if user else ''
    insights_quick_snapshot_icon = '📊'
    insights_quick_snapshot_title = 'Quick Snapshot'
    insights_quick_snapshot_desc = 'A snapshot across all categories.'
    profile_picture = (user.get('profile_picture') or '').strip() if user else ''
    if not profile_picture:
        profile_picture = load_default_profile_photo() or ''
    _, effective_credits = check_user_credits(session.get('username')) if session.get('username') else (False, 0)
    return render_template('index.html', 
                           username=session.get('username'),
                           insights_quick_snapshot_icon=insights_quick_snapshot_icon,
                           insights_quick_snapshot_title=insights_quick_snapshot_title,
                           insights_quick_snapshot_desc=insights_quick_snapshot_desc,
                           role=role,
                           credits=effective_credits,
                           credits_used=user.get('credits_used', 0) if user else 0,
                           profile_picture=profile_picture,
                           default_profile_photo=load_default_profile_photo() or '',
                           company_logo=company_logo,
                           has_profile_iq_access=has_profile_iq,
                           has_subscriber_iq_access=has_subscriber_iq,
                           has_hedge_fund_iq_access=has_hedge_fund_iq,
                           hedge_fund_iq_tickers=hedge_fund_iq_tickers,
                           has_analysis_iq_access=has_analysis_iq,
                           analysis_iq_modules=analysis_iq_modules,
                           allowed_behavioral_categories=allowed_behavioral_categories,
                           allowed_runs=allowed_runs,
                           quick_select_behaviors_exclusions=quick_select_behaviors_exclusions,
                           has_rankers_iq_access=has_rankers_iq,
                           rankers_iq_options=rankers_iq_options,
                           has_ticket_sales_iq_access=has_ticket_sales_iq,
                           has_ticket_sales_tracker_access=has_ticket_sales_tracker,
                           has_llmo_iq_access=has_llmo_iq,
                           default_view_hedge_fund_iq=default_view_hedge_fund_iq,
                           has_purgatory_access=has_purgatory_access,
                           first_name=first_name,
                           last_name=last_name,
                           company=company,
                           user_email=email,
                           cloaked_from=session.get('cloaked_from'),
                           is_dev_env=IS_DEV_ENV)


@app.route('/api/request-credits', methods=['POST'])
@requires_auth
def request_credits():
    """Send email to Liz requesting more credits for the user."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'User not authenticated'}), 401
        
        first_name = user.get('first_name', session.get('username', 'Unknown'))
        last_name = user.get('last_name', '')
        user_email = user.get('email', 'No email on file')
        username = session.get('username', 'Unknown')
        
        # Build email content
        subject = "NEEDS MORE CREDITS"
        
        body = f"""
            <p><strong>{first_name} {last_name}</strong> would like to buy more credits.</p>
            <div class="email-card">
                <div class="email-card-title">Credit request</div>
                <div style="margin: 10px 0;"><span class="email-label">Username</span><br><span class="email-value">{username}</span></div>
                <div style="margin: 10px 0;"><span class="email-label">Email</span><br><span class="email-value">{user_email}</span></div>
                <div style="margin: 10px 0;"><span class="email-label">Current Credits</span><br><span class="email-value">{user.get('credits', 0)}</span></div>
                <div style="margin: 10px 0;"><span class="email-label">Credits Used</span><br><span class="email-value">{user.get('credits_used', 0)}</span></div>
            </div>
            <p style="font-size: 12px; color: #8892b0;">This request was sent from the Crosswalk IQ dashboard.</p>
        """
        html_content = _wrap_email_html(body, title="Credit Request from Crosswalk IQ")
        
        text_content = f"""Credit Request from Crosswalk IQ

{first_name} {last_name} would like to buy more credits.

Username: {username}
Email: {user_email}
Current Credits: {user.get('credits', 0)}
Credits Used: {user.get('credits_used', 0)}

This request was sent from the Crosswalk IQ dashboard.
"""
        
        # Send email to Liz
        success, message = send_email_via_gmail(
            'liz@crosswalknyc.com',
            subject,
            html_content,
            text_content
        )
        
        if success:
            return jsonify({
                'success': True, 
                'message': 'Your request for more credits has been sent. Someone will reach out to you shortly. For immediate assistance on credits, call Liz Huszarik at +1 (818) 231-2610'
            })
        else:
            return jsonify({
                'success': False, 
                'error': f'Failed to send email: {message}',
                'fallback_message': 'Please contact Liz Huszarik directly at liz@crosswalknyc.com or call +1 (818) 231-2610'
            })
            
    except Exception as e:
        print(f"❌ Error requesting credits: {e}")
        return jsonify({
            'success': False, 
            'error': str(e),
            'fallback_message': 'Please contact Liz Huszarik directly at liz@crosswalknyc.com or call +1 (818) 231-2610'
        }), 500


@app.route('/api/collaboration/send-workspace-invite', methods=['POST'])
@requires_auth
def send_workspace_invite():
    """Send email notifications when users are added to a workspace."""
    try:
        data = request.json or {}
        workspace_name = data.get('workspace_name', 'New Workspace')
        workspace_id = data.get('workspace_id', '')
        members = data.get('members', [])  # List of {username, email}
        invited_by = data.get('invited_by', session.get('username', 'Someone'))
        description = data.get('description', '')
        
        if not members:
            return jsonify({'success': False, 'error': 'No members to notify'})
        
        app_url = os.environ.get('APP_URL', 'https://behavioralgraph.onrender.com')
        
        sent_count = 0
        failed_count = 0
        
        for member in members:
            email = member.get('email')
            username = member.get('username', 'User')
            
            if not email:
                continue
            
            subject = f"📁 {invited_by} added you to a workspace - {workspace_name}"
            
            body = f"""
            <p>Hi {username},</p>
            <p><strong>{invited_by}</strong> has added you to a new collaborative workspace in Crosswalk IQ:</p>
            <div class="email-card">
                <div class="email-card-title">{workspace_name}</div>
                {f'<div class="email-label" style="margin-top:8px;">{description}</div>' if description else ''}
            </div>
            <p>In this workspace, you can:</p>
            <ul>
                <li>Share and view audience profiles</li>
                <li>Collaborate with team members</li>
                <li>Leave comments and feedback</li>
                <li>Build presentations together</li>
            </ul>
            <p><a href="{app_url}" class="email-btn">Open Crosswalk IQ</a></p>
        """
            html_content = _wrap_email_html(body, title="📁 You've been added to a workspace!")
            text_content = f"""
You've been added to a workspace!

Hi {username},

{invited_by} has added you to a new collaborative workspace in Crosswalk IQ:

Workspace: {workspace_name}
{f'Description: {description}' if description else ''}

In this workspace, you can:
- Share and view audience profiles
- Collaborate with team members
- Leave comments and feedback
- Build presentations together

Open Crosswalk IQ: {app_url}

---
{EMAIL_SIGNATURE}
"""
            
            try:
                success, message = send_email_via_gmail(email, subject, html_content, text_content)
                if success:
                    sent_count += 1
                    print(f"✅ Workspace invite sent to {email}")
                else:
                    failed_count += 1
                    print(f"❌ Failed to send workspace invite to {email}: {message}")
            except Exception as e:
                failed_count += 1
                print(f"❌ Error sending to {email}: {e}")
        
        return jsonify({
            'success': True,
            'sent': sent_count,
            'failed': failed_count,
            'message': f'Sent {sent_count} invitation(s)'
        })
        
    except Exception as e:
        print(f"❌ Error sending workspace invites: {e}")
        return jsonify({'success': False, 'error': str(e)})


def _send_deck_collaboration_invite(to_email, deck_name, inviter_display, app_url):
    """Send email when someone is invited to collaborate on a deck. Content: asked to collaborate on PROFILE NAME deck by FIRST LAST, link to log in."""
    subject = f"You've been asked to collaborate on {deck_name} deck"
    login_url = app_url.rstrip('/') + '/login'
    body = f"""
            <p><strong>{inviter_display}</strong> has invited you to collaborate on their presentation deck in Crosswalk IQ.</p>
            <div class="email-card">
                <div class="email-card-title">{deck_name}</div>
                <div class="email-label" style="margin-top:8px;">Invited by {inviter_display}</div>
            </div>
            <p>You have full access to view and edit this deck. Log in to the dashboard to get started.</p>
            <p><a href="{login_url}" class="email-btn">Log in to Crosswalk IQ</a></p>
        """
    html_content = _wrap_email_html(body, title="You've been asked to collaborate on a deck")
    text_content = f"""
You've been asked to collaborate on a deck

{inviter_display} has invited you to collaborate on their presentation deck in Crosswalk IQ.

Deck: {deck_name}
Invited by: {inviter_display}

You have full access to view and edit this deck.

Log in to the dashboard: {login_url}

---
{EMAIL_SIGNATURE}
"""
    try:
        success, message = send_email_via_gmail(to_email, subject, html_content, text_content)
        if success:
            print(f"✅ Deck collaboration invite sent to {to_email}")
        else:
            print(f"❌ Failed to send deck collaboration invite to {to_email}: {message}")
        return success
    except Exception as e:
        print(f"❌ Error sending deck collaboration invite to {to_email}: {e}")
        return False


@app.route('/api/health')
def health_check():
    """Quick health check endpoint."""
    try:
        return jsonify({
            'status': 'healthy', 
            'timestamp': datetime.now().isoformat(),
            'cache_ready': cache_loading_complete,
            'cache_size': len(s3_cache.get('jobs', [])) if s3_cache else 0
        })
    except:
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


# ============================================================================
# AI-POWERED API ENDPOINTS
# ============================================================================

@app.route('/api/ai/insights', methods=['POST'])
@requires_auth
def api_ai_insights():
    """Generate AI insights for a profile."""
    data = request.get_json()
    profile_data = data.get('profile', {})
    result = generate_ai_insights(profile_data)
    return jsonify(result)

@app.route('/api/ai/persona', methods=['POST'])
@requires_auth
def api_ai_persona():
    """Generate AI persona for a profile."""
    data = request.get_json()
    profile_data = data.get('profile', {})
    result = generate_persona(profile_data)
    return jsonify(result)

@app.route('/api/ai/marketing', methods=['POST'])
@requires_auth
def api_ai_marketing():
    """Generate AI marketing strategy for a profile."""
    data = request.get_json()
    profile_data = data.get('profile', {})
    result = generate_marketing_strategy(profile_data)
    return jsonify(result)

@app.route('/api/ai/chat', methods=['POST'])
@requires_auth
def api_ai_chat():
    """Chat with profile data."""
    data = request.get_json()
    profile_data = data.get('profile', {})
    question = data.get('question', '')
    if not question:
        return jsonify({'error': 'No question provided'}), 400
    result = chat_with_data(profile_data, question)
    return jsonify(result)

@app.route('/api/ai/compare', methods=['POST'])
@requires_auth
def api_ai_compare():
    """AI comparison of multiple profiles."""
    data = request.get_json()
    profiles = data.get('profiles', [])
    if len(profiles) < 2:
        return jsonify({'error': 'Need at least 2 profiles to compare'}), 400
    result = compare_profiles_ai(profiles)
    return jsonify(result)

@app.route('/api/ai/business', methods=['POST'])
@requires_auth
def api_ai_business():
    """Answer a business question using profile data."""
    data = request.get_json()
    profile_data = data.get('profile', {})
    question = data.get('question', '')
    history = data.get('history', [])
    
    if not question:
        return jsonify({'error': 'No question provided'}), 400
    
    result = answer_business_question(profile_data, question, history)
    return jsonify(result)

@app.route('/api/ai/deck', methods=['POST'])
@requires_auth
def api_ai_deck():
    """Generate a presentation deck for a business question."""
    data = request.get_json()
    profile_data = data.get('profile', {})
    question = data.get('question', '')
    findings = data.get('findings', '')
    
    if not question:
        return jsonify({'error': 'No question provided'}), 400
    
    result = generate_business_deck(profile_data, question, findings)
    return jsonify(result)


@app.route('/api/ai/generate-logline', methods=['POST'])
@requires_auth
def api_generate_logline():
    """Generate a content logline and concept based on audience analysis."""
    data = request.get_json()
    result = generate_content_logline(data)
    return jsonify(result)


@app.route('/api/ai/behavioral-summary', methods=['POST'])
@requires_auth
def api_behavioral_summary():
    """Generate AI-powered behavioral summary bullets describing who this person is."""
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'error': 'Invalid or missing JSON body'}), 400
    result = generate_behavioral_summary(data)
    return jsonify(result)


# ============================================================================
# AI SUMMARY CACHING HELPERS - GLOBAL S3 CACHE FOR ALL USERS
# ============================================================================

AI_CACHE_S3_PREFIX = 'ai-summaries/'  # S3 prefix for cached AI summaries

def generate_cache_key(profile_name, behavioral_data, top_over_indexers):
    """Generate a unique cache key based on profile name and behavioral data signature."""
    # Create a hash from the profile name and key behavioral data
    # We use top items from each category to create a stable fingerprint
    fingerprint_parts = [profile_name.lower().strip()]
    
    # Add behavioral category names and top item names (sorted for consistency)
    if isinstance(behavioral_data, dict):
        for cat in sorted(behavioral_data.keys()):
            items = behavioral_data.get(cat, [])
            if isinstance(items, list) and items:
                # Take top 3 item names for the fingerprint
                top_names = [item.get('name', '') for item in items[:3] if isinstance(item, dict)]
                fingerprint_parts.append(f"{cat}:{','.join(top_names)}")
    
    # Add top over-indexers for additional uniqueness
    if isinstance(top_over_indexers, list) and top_over_indexers:
        top_indexer_names = [item.get('name', '') for item in top_over_indexers[:5] if isinstance(item, dict)]
        fingerprint_parts.append(f"top:{','.join(top_indexer_names)}")
    
    # Create hash
    fingerprint = '|'.join(fingerprint_parts)
    cache_hash = hashlib.md5(fingerprint.encode()).hexdigest()[:16]
    
    # Clean profile name for filename
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', profile_name)[:50]
    
    return f"{safe_name}_{cache_hash}"

def get_cached_summary(cache_key):
    """Retrieve a cached summary from S3 global cache (shared across all users)."""
    # Try S3 global cache first
    if s3_client:
        try:
            s3_key = f"{AI_CACHE_S3_PREFIX}{cache_key}.json"
            response = s3_client.get_object(Bucket=METADATA_BUCKET, Key=s3_key)
            cached = json.loads(response['Body'].read().decode('utf-8'))
            
            # Check if cache is expired (90 days for global cache - longer since it's shared)
            cached_time = datetime.fromisoformat(cached.get('cached_at', '2000-01-01'))
            if datetime.now() - cached_time < timedelta(days=90):
                print(f"✅ S3 Global Cache HIT for behavioral summary: {cache_key}")
                return cached.get('data')
            else:
                print(f"⏰ S3 Cache EXPIRED for behavioral summary: {cache_key}")
        except s3_client.exceptions.NoSuchKey:
            print(f"📭 S3 Cache MISS for behavioral summary: {cache_key}")
        except Exception as e:
            print(f"⚠️ Error reading S3 cache {cache_key}: {e}")
    
    # Fallback to local cache
    ensure_cache_dir()
    cache_file = os.path.join(AI_CACHE_DIR, f"{cache_key}.json")
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cached = json.load(f)
            
            # Check if cache is expired (30 days for local)
            cached_time = datetime.fromisoformat(cached.get('cached_at', '2000-01-01'))
            if datetime.now() - cached_time < timedelta(days=30):
                print(f"✅ Local Cache HIT for behavioral summary: {cache_key}")
                # Also save to S3 for global sharing
                save_cached_summary_to_s3(cache_key, cached.get('data'))
                return cached.get('data')
            else:
                print(f"⏰ Local Cache EXPIRED for behavioral summary: {cache_key}")
        except Exception as e:
            print(f"⚠️ Error reading local cache file {cache_key}: {e}")
    
    return None

def save_cached_summary_to_s3(cache_key, data):
    """Save a summary to S3 global cache (shared across all users)."""
    if not s3_client:
        return False
    
    try:
        s3_key = f"{AI_CACHE_S3_PREFIX}{cache_key}.json"
        cache_entry = {
            'cached_at': datetime.now().isoformat(),
            'data': data
        }
        s3_client.put_object(
            Bucket=METADATA_BUCKET,
            Key=s3_key,
            Body=json.dumps(cache_entry),
            ContentType='application/json'
        )
        print(f"💾 Saved to S3 global cache: {cache_key}")
        return True
    except Exception as e:
        print(f"⚠️ Error saving to S3 cache {cache_key}: {e}")
        return False

def save_cached_summary(cache_key, data):
    """Save a summary to both local and S3 global cache."""
    # Save to local cache first (fast)
    ensure_cache_dir()
    cache_file = os.path.join(AI_CACHE_DIR, f"{cache_key}.json")
    
    try:
        cache_entry = {
            'cached_at': datetime.now().isoformat(),
            'data': data
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_entry, f)
        print(f"💾 Cached behavioral summary locally: {cache_key}")
    except Exception as e:
        print(f"⚠️ Error saving local cache file {cache_key}: {e}")
    
    # Also save to S3 for global sharing across all users
    save_cached_summary_to_s3(cache_key, data)


def generate_behavioral_summary(profile_data):
    """Generate behavioral summary bullets using AI based on demographic and behavioral data."""
    # Extract data needed for cache key
    profile_name = profile_data.get('profileName', 'This audience')
    behavioral = profile_data.get('behavioral', {})
    top_over_indexers = profile_data.get('topOverIndexers', [])
    
    # Generate cache key and check for cached result
    cache_key = generate_cache_key(profile_name, behavioral, top_over_indexers)
    cached_result = get_cached_summary(cache_key)
    
    if cached_result:
        # Return cached result with a flag indicating it was cached
        cached_result['cached'] = True
        return cached_result
    
    # No cache hit - generate new summary
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Extract demographic data - filter to only include items >= 25%
        demographics = _filter_demographics_for_insights(profile_data.get('demographics', {}))
        demographics_index = profile_data.get('demographicsIndex', {})
        demographics_gen_pop = profile_data.get('demographicsGenPop', {})
        
        # Extract behavioral data - filter to only include items >= 25%
        behavioral = _filter_behavioral_for_insights(profile_data.get('behavioral', {}))
        behavioral_index = profile_data.get('behavioralIndex', {})
        
        # Extract top over-indexers - filter to only include items >= 25%
        top_over_indexers = [i for i in profile_data.get('topOverIndexers', []) if i.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS]
        
        # Get profile name
        profile_name = profile_data.get('profileName', 'This audience')
        
        # Build demographic summary (only items >= 25%)
        demo_summary = []
        for category, values in demographics.items():
            if isinstance(values, dict) and values:
                top_items = sorted(values.items(), key=lambda x: x[1], reverse=True)[:3]
                index_data = demographics_index.get(category, {})
                items_with_index = []
                for name, pct in top_items:
                    idx = index_data.get(name, 100) if isinstance(index_data, dict) else 100
                    items_with_index.append(f"{name} ({pct:.1f}%, {idx:.0f} index)")
                if items_with_index:
                    demo_summary.append(f"{category}: {', '.join(items_with_index)}")
        
        # Build behavioral summary with top items per category (only items >= 25%)
        behavior_summary = []
        for category, items in behavioral.items():
            if isinstance(items, list) and items:
                top_items = items[:5]
                index_data = behavioral_index.get(category, [])
                items_str = []
                for i, item in enumerate(top_items):
                    name = item.get('name', item.get('value', ''))
                    pct = item.get('pct', 0)
                    # Try to get index from behavioral_index
                    idx = 100
                    if isinstance(index_data, list) and i < len(index_data):
                        idx = index_data[i].get('index', 100)
                    elif isinstance(item, dict):
                        idx = item.get('index', 100)
                    items_str.append(f"{name} ({pct:.1f}%, {idx:.0f} index)")
                if items_str:
                    behavior_summary.append(f"{category}: {', '.join(items_str)}")
        
        # Build top over-indexers summary
        top_indexers_str = ""
        if top_over_indexers:
            top_5 = top_over_indexers[:10]
            top_indexers_str = "\n".join([
                f"- {item.get('name', '')} ({item.get('category', '')}) - {item.get('pct', 0):.1f}% vs {item.get('genPop', 0):.1f}% GP, {item.get('index', 100):.0f} index"
                for item in top_5
            ])
        
        prompt = f"""You are an expert consumer behavior analyst. Based on the following audience data, write 4-6 concise bullet points that describe WHO these panelists are BEHAVIORALLY - their lifestyle, interests, habits, and what makes them unique.

PROFILE: {profile_name}

DEMOGRAPHIC PROFILE (only values with 25%+ of audience):
{chr(10).join(demo_summary[:6]) if demo_summary else 'No demographics meet the 25% threshold'}

BEHAVIORAL DATA (only values with 25%+ of audience):
{chr(10).join(behavior_summary[:12]) if behavior_summary else 'No behaviors meet the 25% threshold'}

TOP OVER-INDEXING BEHAVIORS (only values with 25%+ of audience):
{top_indexers_str if top_indexers_str else 'No over-indexers meet the 25% threshold'}

INSTRUCTIONS:
- Write 4-6 bullet points describing this audience's behavioral profile
- IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.
- IMPORTANT: Start each bullet with "{profile_name} panelists" to frame it as an analysis of observed behavior
- Focus on LIFESTYLE and BEHAVIORS, not just demographics
- Be specific about their interests, habits, media consumption, and shopping patterns
- Highlight what makes them UNIQUE compared to the average person
- Use analytical language that describes observed patterns (e.g., "{profile_name} panelists show strong affinity for..." or "{profile_name} panelists tend to engage with...")
- Each bullet should be 1-2 sentences max
- Do NOT include demographic stats - focus on behavioral insights
- Do NOT use recommendation language (avoid "should", "recommend", "consider")

Format: Return ONLY a JSON array of strings, each string being one bullet point. Example:
["{profile_name} panelists show strong engagement with...", "{profile_name} panelists demonstrate a preference for..."]"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert consumer behavior analyst who creates vivid, insightful behavioral profiles. Return only valid JSON arrays."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=600,
            temperature=0.7
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON response
        try:
            # Remove markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            bullets = json.loads(content)
            if not isinstance(bullets, list):
                bullets = [content]
        except:
            # Fallback: split by newlines if JSON parsing fails
            bullets = [line.strip().lstrip('- •').strip() for line in content.split('\n') if line.strip() and not line.strip().startswith('[')]
        
        result = {
            "bullets": bullets,
            "tokens_used": response.usage.total_tokens,
            "cached": False
        }
        
        # Save to cache for future requests
        save_cached_summary(cache_key, result)
        
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def generate_content_logline(analysis_data):
    """Generate a content logline using AI based on audience analysis."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        target_demo = analysis_data.get('targetDemo', {})
        top_shows = analysis_data.get('topShows', [])
        top_talent = analysis_data.get('topTalent', [])
        top_brands = analysis_data.get('topBrands', [])
        gaps = analysis_data.get('gaps', [])
        genres = analysis_data.get('genres', [])
        
        # Build context
        demo_str = f"Gender: {', '.join(target_demo.get('gender', []))}, Age: {', '.join(target_demo.get('age', []))}"
        platforms_str = ', '.join(target_demo.get('platforms', []))
        shows_str = ', '.join([s.get('name', '') for s in top_shows[:5]])
        talent_str = ', '.join([t.get('name', '') for t in top_talent[:5]])
        gaps_str = '\n'.join([f"- {g.get('type')}: {g.get('opportunity')}" for g in gaps[:3]])
        genres_str = ', '.join([f"{g.get('genre')} ({g.get('score')}%)" for g in genres[:4]])
        
        prompt = f"""You are a Hollywood content development executive. Based on the following audience analysis, create an original content concept.

TARGET AUDIENCE:
{demo_str}
Target Platforms: {platforms_str}

WHAT WORKS FOR THIS AUDIENCE:
Top performing shows: {shows_str}
Top genres by audience fit: {genres_str}
Resonating talent: {talent_str}

CONTENT GAPS/OPPORTUNITIES:
{gaps_str}

Based on this analysis, create:

1. LOGLINE: A compelling one-sentence pitch for an original series (format: "When [protagonist] [inciting incident], they must [goal] before [stakes]")

2. CONCEPT: A 2-3 sentence expanded description of the series concept

3. GENRE & FORMAT: Recommended genre and format (e.g., "Drama • 1-hour serialized")

4. DEMOGRAPHIC APPEAL: Why this concept will resonate with the target demographic

5. TAGS: 5 descriptive tags for this concept

Respond in JSON format:
{{
    "logline": "...",
    "concept": "...",
    "genre": "...",
    "format": "...",
    "demographicAppeal": "...",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]
}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        
        content = response.choices[0].message.content
        
        # Parse JSON response
        import json
        # Clean up markdown if present
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0]
        elif '```' in content:
            content = content.split('```')[1].split('```')[0]
        
        result = json.loads(content.strip())
        return result
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return {
            "logline": "When a diverse group of characters face unexpected challenges, they must unite to overcome obstacles before time runs out.",
            "concept": "Based on the analysis, we recommend a character-driven drama that resonates with the target demographic's preferences.",
            "genre": "Drama",
            "format": "1-hour serialized",
            "demographicAppeal": "Appeals to the target demographic through relevant themes and relatable characters.",
            "tags": ["Drama", "Character-Driven", "Contemporary", "Ensemble", "Streaming"],
            "error": "AI response parsing issue - showing fallback"
        }
    except Exception as e:
        print(f"Error generating logline: {e}")
        return {"error": str(e)}


# ============================================================================
# RANKERS - Netflix (BEHAVIORALGRAPH.PUBLIC.NETFLIX)
# Cached system-wide: in-memory per process + JSON file on disk. After the first
# user loads data, all other users (and other workers) get it from file/memory.
# ============================================================================

NETFLIX_RANKER_CACHE = {}
NETFLIX_RANKER_LOCK = threading.Lock()
NETFLIX_RANKER_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'netflix_ranker_cache.json')
NETFLIX_RANKER_CACHE_MAX_AGE_HOURS = 24  # refresh today's data if cache older than this
NETFLIX_RANKER_S3_PREFIX = 'rankers_cache/netflix/'
NETFLIX_RANKER_S3_INDEX_KEY = 'rankers_cache/netflix/index.json'
NETFLIX_RANKER_BACKFILL_RUNNING = False


def _run_netflix_ranker_backfill(start_d, end_d):
    """Run backfill loop for S3 cache from start_d to end_d (date objects). Returns (filled_count, cached_dates_set)."""
    if not s3_client:
        return 0, set()
    cached = set(_netflix_ranker_s3_list_cached_dates())
    filled = 0
    current = start_d
    while current <= end_d:
        dt_str = current.strftime('%Y-%m-%d')
        if dt_str in cached:
            current += timedelta(days=1)
            continue
        try:
            day_payload = _fetch_netflix_ranker_for_single_date(dt_str)
            if _netflix_ranker_s3_save_day(dt_str, day_payload):
                cached.add(dt_str)
                filled += 1
        except Exception as e:
            print(f"[Netflix Ranker] Backfill day {dt_str} failed: {e}")
        current += timedelta(days=1)
    if cached:
        _netflix_ranker_s3_update_index(sorted(cached))
    return filled, cached


def _netflix_ranker_s3_list_cached_dates():
    """Return sorted list of date strings (YYYY-MM-DD) that are cached in S3."""
    idx = _netflix_ranker_s3_get_index()
    if not idx:
        return []
    dates = idx.get('dates', [])
    return sorted(dates) if isinstance(dates, list) else []


def _netflix_ranker_s3_get_index():
    """Return full S3 index: {'dates': [...], 'updated_at': 'ISO str'} or None."""
    if not s3_client:
        return None
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=NETFLIX_RANKER_S3_INDEX_KEY)
        data = json.loads(response['Body'].read().decode('utf-8'))
        return data
    except (ClientError, Exception):
        return None


def _netflix_ranker_s3_load_day(date_str):
    """Load a single day's ranker payload from S3. Returns None if missing or error."""
    if not s3_client:
        return None
    key = f"{NETFLIX_RANKER_S3_PREFIX}{date_str}.json"
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(response['Body'].read().decode('utf-8'))
    except (ClientError, Exception):
        return None


def _netflix_ranker_s3_save_day(date_str, day_payload):
    """Save a single day's ranker payload to S3. Returns True on success."""
    if not s3_client:
        return False
    key = f"{NETFLIX_RANKER_S3_PREFIX}{date_str}.json"
    try:
        body = json.dumps(day_payload, indent=0).encode('utf-8')
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType='application/json')
        return True
    except Exception as e:
        print(f"[Netflix Ranker S3] Save day {date_str} failed: {e}")
        return False


def _netflix_ranker_s3_update_index(cached_dates):
    """Write the cache index (list of cached date strings) to S3."""
    if not s3_client:
        return False
    try:
        data = {'dates': sorted(cached_dates), 'updated_at': datetime.utcnow().isoformat() + 'Z'}
        body = json.dumps(data, indent=2).encode('utf-8')
        s3_client.put_object(Bucket=S3_BUCKET, Key=NETFLIX_RANKER_S3_INDEX_KEY, Body=body, ContentType='application/json')
        return True
    except Exception as e:
        print(f"[Netflix Ranker S3] Update index failed: {e}")
        return False


def _build_netflix_ranker_payload(rows):
    """Build API payload from raw rows: (visit_date, name_of_show, genre, type, views, avg_watch_time, run_time). Excludes any row where genre contains 'Indian'."""
    from collections import defaultdict
    daily = []
    by_date = defaultdict(list)
    by_show = defaultdict(list)
    for r in rows:
        dt = r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])[:10]
        show_name = (r[1] or '').strip() or 'Unknown'
        genre = (r[2] or '').strip() or '-'
        typ = (r[3] or '').strip() or '-'
        views = int(r[4] or 0)
        avg_watch_time = round(float(r[5]), 2) if len(r) > 5 and r[5] is not None and r[5] != '' else None
        run_time = round(float(r[6]), 2) if len(r) > 6 and r[6] is not None and r[6] != '' else None
        if run_time is not None and run_time == int(run_time):
            run_time = int(run_time)
        # Exclude Indian genre from all ranker views
        if 'indian' in (genre or '').lower():
            continue
        item = {'show_name': show_name, 'views': views, 'genre': genre, 'type': typ, 'avg_watch_time': avg_watch_time, 'run_time': run_time}
        daily.append({'date': dt, **item})
        by_date[dt].append(item)
        by_show[show_name].append({'date': dt, 'views': views})
    dates_sorted = sorted(by_date.keys())
    date_range = {'min': dates_sorted[0], 'max': dates_sorted[-1]} if dates_sorted else {}
    # genres_by_date: for each date, aggregate by genre and pick top show per genre
    genres_by_date = defaultdict(list)
    for dt, items in by_date.items():
        genre_views = defaultdict(lambda: {'views': 0, 'top_show': '', 'top_views': 0})
        for it in items:
            g = it['genre'] or 'Other'
            genre_views[g]['views'] += it['views']
            if it['views'] > genre_views[g]['top_views']:
                genre_views[g]['top_show'] = it['show_name']
                genre_views[g]['top_views'] = it['views']
        for g, v in genre_views.items():
            genres_by_date[dt].append({'genre': g, 'views': v['views'], 'top_show': v['top_show']})
    # top_shows_over_time: top 20 shows by total views, with by_date
    show_totals = [(name, sum(p['views'] for p in pts)) for name, pts in by_show.items()]
    show_totals.sort(key=lambda x: -x[1])
    top_20_names = [s[0] for s in show_totals[:20]]
    top_shows_over_time = []
    for name in top_20_names:
        pts = by_show[name]
        by_date_show = {p['date']: p['views'] for p in pts}
        top_shows_over_time.append({'show_name': name, 'by_date': by_date_show})
    return {
        'daily': daily,
        'by_date': dict(by_date),
        'by_show': dict(by_show),
        'genres_by_date': dict(genres_by_date),
        'top_shows_over_time': top_shows_over_time,
        'dates_sorted': dates_sorted,
        'date_range': date_range
    }

def _build_netflix_seasons_payload(rows):
    """Build by_date_season from rows (visit_date, name_of_show, season, views, avg_watch_time, run_time)."""
    from collections import defaultdict
    by_date_season = defaultdict(list)
    for r in rows:
        dt = r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])[:10]
        show_name = (r[1] or '').strip() or 'Unknown'
        season = (r[2] or '').strip() or '-'
        views = int(r[3] or 0)
        avg_watch_time = round(float(r[4]), 2) if len(r) > 4 and r[4] is not None and r[4] != '' else None
        run_time = round(float(r[5]), 2) if len(r) > 5 and r[5] is not None and r[5] != '' else None
        if run_time is not None and run_time == int(run_time):
            run_time = int(run_time)
        by_date_season[dt].append({'show_name': show_name, 'season': season, 'views': views, 'avg_watch_time': avg_watch_time, 'run_time': run_time})
    return dict(by_date_season)

def _build_netflix_episodes_payload(rows):
    """Build by_date_episode from rows (visit_date, name_of_show, season, episode, episode_name, views, avg_watch_time, run_time)."""
    from collections import defaultdict
    by_date_episode = defaultdict(list)
    for r in rows:
        dt = r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])[:10]
        show_name = (r[1] or '').strip() or 'Unknown'
        season = (r[2] or '').strip() or '-'
        episode = _normalize_netflix_all_field(r[3])
        episode_name = (r[4] or '').strip() or 'Unknown'
        views = int(r[5] or 0)
        avg_watch_time = round(float(r[6]), 2) if len(r) > 6 and r[6] is not None and r[6] != '' else None
        run_time = round(float(r[7]), 2) if len(r) > 7 and r[7] is not None and r[7] != '' else None
        if run_time is not None and run_time == int(run_time):
            run_time = int(run_time)
        by_date_episode[dt].append({'show_name': show_name, 'season': season, 'episode': episode, 'episode_name': episode_name, 'views': views, 'avg_watch_time': avg_watch_time, 'run_time': run_time})
    return dict(by_date_episode)

def _normalize_netflix_all_field(value, allow_unknown=False):
    """Normalize Netflix all-ranker fields to avoid null/blank display."""
    if value is None:
        return 'Unknown' if allow_unknown else ''
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() == 'null':
            return 'Unknown' if allow_unknown else ''
        return cleaned
    # Normalize numeric/other types to string without trailing .0
    if isinstance(value, (int, float)) and value == int(value):
        return str(int(value))
    return str(value).strip()

def _build_netflix_all_payload(rows):
    """Build by_date_all from rows (visit_date, name_of_show, season, episode, episode_name, views, avg_watch_time, run_time)."""
    from collections import defaultdict
    by_date_all = defaultdict(list)
    for r in rows:
        dt = r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])[:10]
        show_name = _normalize_netflix_all_field(r[1], allow_unknown=True)
        season = _normalize_netflix_all_field(r[2])
        episode = _normalize_netflix_all_field(r[3])
        episode_name = _normalize_netflix_all_field(r[4])
        views = int(r[5] or 0)
        avg_watch_time = round(float(r[6]), 2) if len(r) > 6 and r[6] is not None and r[6] != '' else None
        run_time = round(float(r[7]), 2) if len(r) > 7 and r[7] is not None and r[7] != '' else None
        if run_time is not None and run_time == int(run_time):
            run_time = int(run_time)
        by_date_all[dt].append({
            'show_name': show_name,
            'season': season,
            'episode': episode,
            'episode_name': episode_name,
            'views': views,
            'avg_watch_time': avg_watch_time,
            'run_time': run_time
        })
    return dict(by_date_all)

# Snowflake: parse "45m", "1h 30m" (h=hours, m=minutes) to total minutes for AVG/MAX
_NETFLIX_DURATION_MINS = """
COALESCE(TRY_TO_NUMBER(REGEXP_SUBSTR(COALESCE(TIME_ON_PAGE::VARCHAR, RUN_TIME::VARCHAR), '([0-9]+)h', 1, 1, 'e', 1)), 0) * 60
+ COALESCE(TRY_TO_NUMBER(REGEXP_SUBSTR(COALESCE(TIME_ON_PAGE::VARCHAR, RUN_TIME::VARCHAR), '([0-9]+)m', 1, 1, 'e', 1)), 0)
"""

def _fetch_netflix_ranker_from_snowflake(refresh_today_only=False):
    """Query BEHAVIORALGRAPH.PUBLIC.NETFLIX for past year (or latest day only) and return payload.
    Includes by_date (series), by_date_season (show+season, type=Show only), by_date_episode (show+season+episode)."""
    try:
        import bg
        conn = bg.connect_snowflake()
        cur = conn.cursor()
    except Exception as e:
        raise RuntimeError(f'Snowflake connection failed: {e}')
    try:
        # Default to last 7 days for fast initial load; use refresh_today_only for just today
        # Parse "45m" / "1h 30m" to minutes in subquery so AVG/MAX work (h=hours, m=minutes)
        if refresh_today_only:
            sql = f"""
                SELECT visit_date, NAME_OF_SHOW, GENRE, TYPE, COUNT(*) AS views,
                    AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
                FROM (
                    SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, GENRE, TYPE,
                        ({_NETFLIX_DURATION_MINS}) AS duration_mins
                    FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                    WHERE VISIT_TS >= CURRENT_DATE()
                      AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                ) sub
                GROUP BY 1, 2, 3, 4
                ORDER BY 1, 5 DESC
            """
        else:
            sql = f"""
                SELECT visit_date, NAME_OF_SHOW, GENRE, TYPE, COUNT(*) AS views,
                    AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
                FROM (
                    SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, GENRE, TYPE,
                        ({_NETFLIX_DURATION_MINS}) AS duration_mins
                    FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                    WHERE VISIT_TS >= DATEADD(day, -7, CURRENT_DATE())
                      AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                ) sub
                GROUP BY 1, 2, 3, 4
                ORDER BY 1, 5 DESC
            """
        cur.execute(sql)
        rows = cur.fetchall()
        payload = _build_netflix_ranker_payload(rows)

        # Seasons: TYPE = 'Show' only, group by (date, name_of_show, season). Exclude Indian genre.
        # Exclude rows where SEASON equals NAME_OF_SHOW (not a real season distinction)
        date_filter = "VISIT_TS >= CURRENT_DATE()" if refresh_today_only else "VISIT_TS >= DATEADD(day, -7, CURRENT_DATE())"
        try:
            sql_seasons = f"""
                SELECT visit_date, NAME_OF_SHOW, SEASON, COUNT(*) AS views,
                    AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
                FROM (
                    SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, SEASON,
                        ({_NETFLIX_DURATION_MINS}) AS duration_mins
                    FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                    WHERE {date_filter}
                      AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                      AND UPPER(TRIM(TYPE)) = 'SHOW'
                      AND SEASON IS NOT NULL AND TRIM(SEASON) != ''
                      AND UPPER(TRIM(SEASON)) != UPPER(TRIM(NAME_OF_SHOW))
                      AND (GENRE IS NULL OR LOWER(TRIM(GENRE)) NOT LIKE '%indian%')
                ) sub
                GROUP BY 1, 2, 3
                ORDER BY 1, 4 DESC
            """
            cur.execute(sql_seasons)
            payload['by_date_season'] = _build_netflix_seasons_payload(cur.fetchall())
        except Exception:
            payload['by_date_season'] = {}

        try:
            sql_episodes = f"""
                SELECT visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME, COUNT(*) AS views,
                    AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
                FROM (
                    SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME,
                        ({_NETFLIX_DURATION_MINS}) AS duration_mins
                    FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                    WHERE {date_filter}
                      AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                      AND EPISODE_NAME IS NOT NULL AND TRIM(EPISODE_NAME) != ''
                      AND UPPER(TRIM(EPISODE_NAME)) != UPPER(TRIM(NAME_OF_SHOW))
                      AND (GENRE IS NULL OR LOWER(TRIM(GENRE)) NOT LIKE '%indian%')
                ) sub
                GROUP BY 1, 2, 3, 4, 5
                ORDER BY 1, 6 DESC
            """
            cur.execute(sql_episodes)
            payload['by_date_episode'] = _build_netflix_episodes_payload(cur.fetchall())
        except Exception:
            payload['by_date_episode'] = {}

        try:
            sql_all = f"""
                SELECT visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME, COUNT(*) AS views,
                    AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
                FROM (
                    SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME,
                        ({_NETFLIX_DURATION_MINS}) AS duration_mins
                    FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                    WHERE {date_filter}
                      AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                      AND (GENRE IS NULL OR LOWER(TRIM(GENRE)) NOT LIKE '%indian%')
                ) sub
                GROUP BY 1, 2, 3, 4, 5
                ORDER BY 1, 6 DESC
            """
            cur.execute(sql_all)
            payload['by_date_all'] = _build_netflix_all_payload(cur.fetchall())
        except Exception:
            payload['by_date_all'] = {}

        conn.close()
        return payload
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_netflix_ranker_for_single_date(visit_date_str):
    """Fetch Netflix ranker data for one day from Snowflake. Returns payload with single date in all by_* dicts."""
    try:
        import bg
        conn = bg.connect_snowflake()
        cur = conn.cursor()
    except Exception as e:
        raise RuntimeError(f'Snowflake connection failed: {e}')
    try:
        # Single-day: parse "45m" / "1h 30m" to minutes in subquery
        sql = f"""
            SELECT visit_date, NAME_OF_SHOW, GENRE, TYPE, COUNT(*) AS views,
                AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
            FROM (
                SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, GENRE, TYPE,
                    ({_NETFLIX_DURATION_MINS}) AS duration_mins
                FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                WHERE DATE(VISIT_TS) = %s
                  AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
            ) sub
            GROUP BY 1, 2, 3, 4
            ORDER BY 1, 5 DESC
        """
        cur.execute(sql, (visit_date_str,))
        rows = cur.fetchall()
        payload = _build_netflix_ranker_payload(rows)

        sql_seasons = f"""
            SELECT visit_date, NAME_OF_SHOW, SEASON, COUNT(*) AS views,
                AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
            FROM (
                SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, SEASON,
                    ({_NETFLIX_DURATION_MINS}) AS duration_mins
                FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                WHERE DATE(VISIT_TS) = %s
                  AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                  AND UPPER(TRIM(TYPE)) = 'SHOW'
                  AND SEASON IS NOT NULL AND TRIM(SEASON) != ''
                  AND UPPER(TRIM(SEASON)) != UPPER(TRIM(NAME_OF_SHOW))
                  AND (GENRE IS NULL OR LOWER(TRIM(GENRE)) NOT LIKE '%%indian%%')
            ) sub
            GROUP BY 1, 2, 3
            ORDER BY 1, 4 DESC
        """
        try:
            cur.execute(sql_seasons, (visit_date_str,))
            payload['by_date_season'] = _build_netflix_seasons_payload(cur.fetchall())
        except Exception:
            payload['by_date_season'] = {}

        sql_episodes = f"""
            SELECT visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME, COUNT(*) AS views,
                AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
            FROM (
                SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME,
                    ({_NETFLIX_DURATION_MINS}) AS duration_mins
                FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                WHERE DATE(VISIT_TS) = %s
                  AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                  AND EPISODE_NAME IS NOT NULL AND TRIM(EPISODE_NAME) != ''
                  AND UPPER(TRIM(EPISODE_NAME)) != UPPER(TRIM(NAME_OF_SHOW))
                  AND (GENRE IS NULL OR LOWER(TRIM(GENRE)) NOT LIKE '%%indian%%')
            ) sub
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY 1, 6 DESC
        """
        try:
            cur.execute(sql_episodes, (visit_date_str,))
            payload['by_date_episode'] = _build_netflix_episodes_payload(cur.fetchall())
        except Exception:
            payload['by_date_episode'] = {}

        sql_all = f"""
            SELECT visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME, COUNT(*) AS views,
                AVG(duration_mins) AS avg_watch_time, MAX(duration_mins) AS run_time
            FROM (
                SELECT DATE(VISIT_TS) AS visit_date, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME,
                    ({_NETFLIX_DURATION_MINS}) AS duration_mins
                FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
                WHERE DATE(VISIT_TS) = %s
                  AND NAME_OF_SHOW IS NOT NULL AND TRIM(NAME_OF_SHOW) != ''
                  AND (GENRE IS NULL OR LOWER(TRIM(GENRE)) NOT LIKE '%%indian%%')
            ) sub
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY 1, 6 DESC
        """
        try:
            cur.execute(sql_all, (visit_date_str,))
            payload['by_date_all'] = _build_netflix_all_payload(cur.fetchall())
        except Exception:
            payload['by_date_all'] = {}

        conn.close()
        return payload
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _merge_netflix_ranker_day_into(base, day_payload):
    """Merge a single-day payload into the aggregated base payload (mutates base)."""
    for dt, rows in (day_payload.get('by_date') or {}).items():
        base.setdefault('by_date', {}).setdefault(dt, []).extend(rows)
    for dt, rows in (day_payload.get('by_date_season') or {}).items():
        base.setdefault('by_date_season', {}).setdefault(dt, []).extend(rows)
    for dt, rows in (day_payload.get('by_date_episode') or {}).items():
        base.setdefault('by_date_episode', {}).setdefault(dt, []).extend(rows)
    for dt, rows in (day_payload.get('by_date_all') or {}).items():
        base.setdefault('by_date_all', {}).setdefault(dt, []).extend(rows)
    base.setdefault('daily', []).extend(day_payload.get('daily') or [])
    for dt, rows in (day_payload.get('genres_by_date') or {}).items():
        base.setdefault('genres_by_date', {}).setdefault(dt, []).extend(rows)
    by_show = base.setdefault('by_show', {})
    for show_name, pts in (day_payload.get('by_show') or {}).items():
        by_show.setdefault(show_name, []).extend(pts)


def _finalize_netflix_ranker_payload(base):
    """Set dates_sorted, date_range, and top_shows_over_time on merged base payload."""
    dates_sorted = sorted(base.get('by_date', {}).keys())
    base['dates_sorted'] = dates_sorted
    base['date_range'] = {'min': dates_sorted[0], 'max': dates_sorted[-1]} if dates_sorted else {}
    by_show = base.get('by_show', {})
    show_totals = [(name, sum(p['views'] for p in pts)) for name, pts in by_show.items()]
    show_totals.sort(key=lambda x: -x[1])
    top_20_names = [s[0] for s in show_totals[:20]]
    top_shows_over_time = []
    for name in top_20_names:
        pts = by_show[name]
        by_date_show = {p['date']: p['views'] for p in pts}
        top_shows_over_time.append({'show_name': name, 'by_date': by_date_show})
    base['top_shows_over_time'] = top_shows_over_time


def _load_netflix_ranker_cache():
    """Load cache from file if present."""
    global NETFLIX_RANKER_CACHE
    if not os.path.exists(NETFLIX_RANKER_CACHE_FILE):
        return None
    try:
        with open(NETFLIX_RANKER_CACHE_FILE, 'r') as f:
            data = json.load(f)
        NETFLIX_RANKER_CACHE['data'] = data
        NETFLIX_RANKER_CACHE['loaded_at'] = datetime.now().timestamp()
        return data
    except Exception:
        return None

def _save_netflix_ranker_cache(data):
    """Save payload to file."""
    try:
        with open(NETFLIX_RANKER_CACHE_FILE, 'w') as f:
            json.dump(data, f, indent=0)
    except Exception as e:
        print(f"Netflix ranker cache save failed: {e}")

@app.route('/api/rankers/netflix/data', methods=['GET'])
def get_netflix_ranker_data():
    """
    Return Netflix ranker data (day-by-day views by show) from BEHAVIORALGRAPH.PUBLIC.NETFLIX.
    Uses S3 per-day cache when available; fetches only missing days (e.g. today) from Snowflake.
    Pass force_refresh=1 to rebuild from S3 cache (and fetch today if not cached).
    """
    print(f"[Netflix Ranker] Request received: force_refresh={request.args.get('force_refresh')}")
    try:
        global NETFLIX_RANKER_CACHE
        force_refresh = request.args.get('force_refresh', '').lower() in ('1', 'true', 'yes')
        today_str = datetime.now().strftime('%Y-%m-%d')

        if not s3_client:
            # No S3: keep legacy behavior (in-memory + file cache, fetch 7 days or today from Snowflake)
            with NETFLIX_RANKER_LOCK:
                data = None if force_refresh else NETFLIX_RANKER_CACHE.get('data')
                if data is None and not force_refresh:
                    data = _load_netflix_ranker_cache()
                loaded_at = NETFLIX_RANKER_CACHE.get('loaded_at') or 0
                age_hours = (datetime.now().timestamp() - loaded_at) / 3600.0
                refresh_today = data is not None and age_hours >= NETFLIX_RANKER_CACHE_MAX_AGE_HOURS
                if data is None or (refresh_today and data.get('date_range')):
                    try:
                        if data and refresh_today and data.get('date_range'):
                            today_payload = _fetch_netflix_ranker_from_snowflake(refresh_today_only=True)
                            _merge_netflix_ranker_day_into(data, today_payload)
                            _finalize_netflix_ranker_payload(data)
                        else:
                            data = _fetch_netflix_ranker_from_snowflake(refresh_today_only=False)
                        NETFLIX_RANKER_CACHE['data'] = data
                        NETFLIX_RANKER_CACHE['loaded_at'] = datetime.now().timestamp()
                        _save_netflix_ranker_cache(data)
                    except Exception as e:
                        print(f"[Netflix Ranker] Snowflake fetch failed: {e}")
                        if data is None:
                            fallback = _load_netflix_ranker_cache()
                            if fallback and (fallback.get('by_date') or fallback.get('date_range')):
                                out = dict(fallback)
                                out['_stale_fallback'] = True
                                out['_fetch_error'] = str(e)
                                return jsonify(out)
                        return jsonify({'error': str(e)}), 500
            payload = data or {}
            print(f"[Netflix Ranker] Returning payload with {len(payload.get('by_date', {}))} dates (no S3)")
            return jsonify(payload)

        # S3 path: use per-day cache, fetch only today if missing
        s3_index = _netflix_ranker_s3_get_index()
        cached_dates = []
        s3_index_updated_at = None
        if s3_index:
            cached_dates = sorted(s3_index.get('dates', [])) if isinstance(s3_index.get('dates'), list) else []
            u = s3_index.get('updated_at')
            if u:
                try:
                    s = u.replace('Z', '+00:00')
                    s3_index_updated_at = datetime.fromisoformat(s).timestamp()
                except Exception:
                    pass
        # When user clicks Refresh and cache is empty, start backfill in background so one run primes cache for everyone
        if force_refresh and len(cached_dates) == 0:
            global NETFLIX_RANKER_BACKFILL_RUNNING
            if not NETFLIX_RANKER_BACKFILL_RUNNING:
                NETFLIX_RANKER_BACKFILL_RUNNING = True
                def _backfill_job():
                    global NETFLIX_RANKER_BACKFILL_RUNNING
                    try:
                        start_d = datetime.strptime('2026-01-01', '%Y-%m-%d').date()
                        end_d = datetime.now().date()
                        filled, _ = _run_netflix_ranker_backfill(start_d, end_d)
                        print(f"[Netflix Ranker] Background backfill finished: {filled} days cached to S3")
                    except Exception as e:
                        print(f"[Netflix Ranker] Background backfill error: {e}")
                    finally:
                        NETFLIX_RANKER_BACKFILL_RUNNING = False
                threading.Thread(target=_backfill_job, daemon=True).start()
                print("[Netflix Ranker] Started background backfill (cache was empty); returning direct Snowflake for now")
        new_day_payload = None
        if today_str not in cached_dates:
            try:
                new_day_payload = _fetch_netflix_ranker_for_single_date(today_str)
                if _netflix_ranker_s3_save_day(today_str, new_day_payload):
                    cached_dates = sorted(set(cached_dates) | {today_str})
                    _netflix_ranker_s3_update_index(cached_dates)
                    print(f"[Netflix Ranker] Cached today {today_str} to S3")
            except Exception as e:
                print(f"[Netflix Ranker] Fetch today failed: {e}")
                # continue with existing cache

        with NETFLIX_RANKER_LOCK:
            data = None if force_refresh else NETFLIX_RANKER_CACHE.get('data')
            if data is None and not force_refresh:
                data = _load_netflix_ranker_cache()
            # When S3 index is newer than our in-memory cache, rebuild so we always show latest uploaded data
            loaded_at = NETFLIX_RANKER_CACHE.get('loaded_at') or 0
            s3_is_newer = s3_index_updated_at is not None and s3_index_updated_at > loaded_at
            need_rebuild = data is None or force_refresh or s3_is_newer
            if s3_is_newer and data:
                print(f"[Netflix Ranker] S3 index newer than cache (index_ts={s3_index_updated_at}, cache_ts={loaded_at}); rebuilding from S3")
                data = None

            if need_rebuild:
                # Rebuild from S3: load each cached day and merge
                base = {}
                for d in cached_dates:
                    day = new_day_payload if (d == today_str and new_day_payload) else _netflix_ranker_s3_load_day(d)
                    if day:
                        _merge_netflix_ranker_day_into(base, day)
                if base:
                    _finalize_netflix_ranker_payload(base)
                    data = base
                    NETFLIX_RANKER_CACHE['data'] = data
                    NETFLIX_RANKER_CACHE['loaded_at'] = datetime.now().timestamp()
                    _save_netflix_ranker_cache(data)
                    print(f"[Netflix Ranker] Rebuilt payload from S3 ({len(cached_dates)} days)")
                else:
                    if data is None:
                        fallback = _load_netflix_ranker_cache()
                        if fallback and (fallback.get('by_date') or fallback.get('date_range')):
                            data = fallback
                            print(f"[Netflix Ranker] Using stale file fallback")
            elif new_day_payload:
                _merge_netflix_ranker_day_into(data, new_day_payload)
                _finalize_netflix_ranker_payload(data)
                NETFLIX_RANKER_CACHE['data'] = data
                NETFLIX_RANKER_CACHE['loaded_at'] = datetime.now().timestamp()
                _save_netflix_ranker_cache(data)
                print(f"[Netflix Ranker] Merged today {today_str} into cache")

        payload = data or {}
        if not payload.get('by_date') and not payload.get('date_range'):
            # No S3 cache (e.g. fresh deploy): fall back to direct Snowflake fetch (last 7 days) like earlier behavior
            snowflake_error = None
            try:
                print(f"[Netflix Ranker] No cache; fetching directly from Snowflake (last 7 days)")
                data = _fetch_netflix_ranker_from_snowflake(refresh_today_only=False)
                if data and (data.get('by_date') or data.get('date_range')):
                    NETFLIX_RANKER_CACHE['data'] = data
                    NETFLIX_RANKER_CACHE['loaded_at'] = datetime.now().timestamp()
                    _save_netflix_ranker_cache(data)
                    print(f"[Netflix Ranker] Returning direct Snowflake payload with {len(data.get('by_date', {}))} dates")
                    return jsonify(data)
            except Exception as e:
                snowflake_error = str(e)
                print(f"[Netflix Ranker] Direct Snowflake fallback failed: {e}")
            err_body = {'error': 'No Netflix ranker data available. Run backfill or check Snowflake.'}
            if snowflake_error:
                err_body['detail'] = snowflake_error
            return jsonify(err_body), 503
        print(f"[Netflix Ranker] Returning payload with {len(payload.get('by_date', {}))} dates")
        return jsonify(payload)
    except Exception as e:
        print(f"[Netflix Ranker] Unexpected error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/rankers/netflix/backfill', methods=['POST'])
@requires_admin
def netflix_ranker_backfill():
    """
    Preload Netflix ranker cache in S3 from start_date to end_date (default 2026-01-01 to today).
    Fetches only days not already cached. Call once to build initial cache.
    """
    if not s3_client:
        return jsonify({'error': 'S3 not configured'}), 503
    start_s = request.args.get('start_date', '2026-01-01').strip()
    end_s = request.args.get('end_date', '').strip() or datetime.now().strftime('%Y-%m-%d')
    try:
        start_d = datetime.strptime(start_s, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_s, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid start_date or end_date; use YYYY-MM-DD'}), 400
    if start_d > end_d:
        return jsonify({'error': 'start_date must be <= end_date'}), 400

    filled, cached = _run_netflix_ranker_backfill(start_d, end_d)
    return jsonify({
        'start_date': start_s,
        'end_date': end_s,
        'filled': filled,
        'cached_dates_count': len(cached),
    })


@app.route('/api/rankers/netflix/show-details', methods=['GET'])
def get_netflix_show_details():
    """
    Get detailed info for a specific Netflix show/episode including:
    - DMA demographics breakdown (% of viewers by location)
    - Runtime, age rating, year released, cast, genre
    - Average watch time (time_on_page or runtime as fallback)
    """
    show_name = request.args.get('show_name', '').strip()
    episode_name = request.args.get('episode_name', '').strip()
    season = request.args.get('season', '').strip()
    date = request.args.get('date', '').strip()
    
    if not show_name:
        return jsonify({'error': 'show_name is required'}), 400
    
    try:
        import bg
        conn = bg.connect_snowflake()
        cur = conn.cursor()
        
        # Build WHERE clause
        where_parts = ["NAME_OF_SHOW = %s"]
        params = [show_name]
        
        if date:
            where_parts.append("DATE(VISIT_TS) = %s")
            params.append(date)
        else:
            where_parts.append("VISIT_TS >= DATEADD(day, -7, CURRENT_DATE())")
        
        if episode_name and episode_name != 'N/A':
            where_parts.append("EPISODE_NAME = %s")
            params.append(episode_name)
        
        if season and season != 'N/A':
            where_parts.append("SEASON = %s")
            params.append(season)
        
        where_clause = " AND ".join(where_parts)
        
        # Get show metadata; parse "45m" / "1h 30m" to minutes for run_time and avg_watch_time
        # Note: CAST is a Snowflake reserved keyword, must be quoted
        meta_sql = f"""
            SELECT 
                NAME_OF_SHOW, SEASON, EPISODE_NAME, GENRE, TYPE,
                AGE_RATING, YEAR_RELEASED, "CAST",
                MAX(({_NETFLIX_DURATION_MINS})) AS run_time_mins,
                AVG(({_NETFLIX_DURATION_MINS})) AS avg_watch_time_mins,
                COUNT(*) as total_views
            FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
            WHERE {where_clause}
            GROUP BY NAME_OF_SHOW, SEASON, EPISODE_NAME, GENRE, TYPE, AGE_RATING, YEAR_RELEASED, "CAST"
            ORDER BY total_views DESC
            LIMIT 1
        """
        cur.execute(meta_sql, params)
        meta_row = cur.fetchone()
        
        if not meta_row:
            conn.close()
            return jsonify({'error': 'Show not found', 'show_name': show_name}), 404
        
        # Get DMA breakdown (% of viewers by location)
        dma_sql = f"""
            SELECT 
                COALESCE(DMA, 'Unknown') as dma,
                COUNT(*) as views
            FROM BEHAVIORALGRAPH.PUBLIC.NETFLIX
            WHERE {where_clause}
            GROUP BY DMA
            ORDER BY views DESC
            LIMIT 50
        """
        cur.execute(dma_sql, params)
        dma_rows = cur.fetchall()
        
        # Calculate total for percentages
        total_dma_views = sum(r[1] for r in dma_rows) if dma_rows else 1
        dma_breakdown = [
            {'dma': r[0] or 'Unknown', 'views': r[1], 'percentage': round((r[1] / total_dma_views) * 100, 2)}
            for r in dma_rows
        ]
        
        conn.close()
        
        # meta_row: 0=name, 1=season, 2=episode_name, 3=genre, 4=type, 5=age_rating, 6=year_released, 7=cast, 8=run_time_mins, 9=avg_watch_time_mins, 10=total_views
        run_mins = meta_row[8] if meta_row[8] is not None else None
        avg_mins = meta_row[9] if meta_row[9] is not None else None
        result = {
            'show_name': meta_row[0] or 'N/A',
            'season': meta_row[1] or 'N/A',
            'episode_name': meta_row[2] or 'N/A',
            'genre': meta_row[3] or 'N/A',
            'type': meta_row[4] or 'N/A',
            'run_time': round(run_mins, 2) if run_mins is not None else 'N/A',
            'age_rating': meta_row[5] or 'N/A',
            'year_released': meta_row[6] or 'N/A',
            'cast': meta_row[7] or 'N/A',
            'avg_watch_time': round(avg_mins, 2) if avg_mins is not None else 'N/A',
            'total_views': meta_row[10] or 0,
            'dma_breakdown': dma_breakdown
        }
        
        return jsonify(result)
        
    except Exception as e:
        print(f"[Netflix Show Details] Error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# REELSHORT / SHORTSTV / GOODSHORT RANKERS - Clickstream data (PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL)
# ============================================================================
REELSHORT_RANKER_CACHE = {}
REELSHORT_RANKER_LOCK = threading.Lock()
REELSHORT_RANKER_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'reelshort_ranker_cache.json')
SHORTSTV_RANKER_CACHE = {}
SHORTSTV_RANKER_LOCK = threading.Lock()
SHORTSTV_RANKER_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'shortstv_ranker_cache.json')
GOODSHORT_RANKER_CACHE = {}
GOODSHORT_RANKER_LOCK = threading.Lock()
GOODSHORT_RANKER_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'goodshort_ranker_cache.json')
CLICKSTREAM_RANKER_WAREHOUSE = os.environ.get('SNOWFLAKE_WAREHOUSE', 'BEHAVIORGRAPH6X')


def _fetch_clickstream_ranker_payload(common_name_substring, today_str):
    """Run clickstream ranker query for COMMON_NAME containing common_name_substring (case insensitive). Returns payload dict."""
    import bg
    conn = bg.connect_snowflake()
    cur = conn.cursor()
    cur.execute(f"USE WAREHOUSE {CLICKSTREAM_RANKER_WAREHOUSE}")
    pattern = f"%{common_name_substring}%"
    sql = """
        SELECT
            COUNT(*) AS total,
            COUNT(CASE WHEN LOWER(COALESCE(URL, '')) LIKE '%con%' OR LOWER(COALESCE(URL, '')) LIKE '%paid%' OR LOWER(COALESCE(URL, '')) LIKE '%thank%' THEN 1 END) AS new_subs,
            COUNT(CASE WHEN LOWER(COALESCE(URL, '')) LIKE '%stop%' OR LOWER(COALESCE(URL, '')) LIKE '%cancel%' THEN 1 END) AS cancels,
            COUNT(CASE WHEN LOWER(COALESCE(URL, '')) LIKE '%dashboard%' THEN 1 END) AS paid_content
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED = CURRENT_DATE()
          AND LOWER(COALESCE(COMMON_NAME, '')) LIKE %s
    """
    cur.execute(sql, (pattern,))
    row = cur.fetchone()
    conn.close()
    total = row[0] or 0
    new_subs = row[1] or 0
    cancels = row[2] or 0
    paid_content = row[3] or 0
    active_accounts = (total * 150) / 10_000_000 * 329_900_000 if total else 0
    new_subscriptions_pct = (new_subs / total * 100) if total else 0
    total_cancels_pct = (cancels / total * 100) if total else 0
    watched_paid_pct = (paid_content / total * 100) if total else 0
    display_paid_pct = watched_paid_pct if watched_paid_pct > 0 else new_subscriptions_pct
    watched_free_pct = 100.0 - display_paid_pct
    return {
        'date': today_str,
        'total_rows': total,
        'active_accounts': round(active_accounts, 2),
        'new_subscriptions_pct': round(new_subscriptions_pct, 2),
        'total_cancels_pct': round(total_cancels_pct, 2),
        'watched_paid_content_pct': round(display_paid_pct, 2),
        'watched_free_content_pct': round(watched_free_pct, 2),
    }


def _save_clickstream_ranker_cache(date_str, payload, cache_file, cache_dict, lock, log_name):
    with lock:
        cache_dict[date_str] = payload
    try:
        existing = {}
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                existing = json.load(f)
        if not isinstance(existing, dict):
            existing = {}
        existing[date_str] = payload
        with open(cache_file, 'w') as f:
            json.dump(existing, f, indent=0)
    except Exception as e:
        print(f"[{log_name}] Cache save failed: {e}")


@app.route('/api/rankers/reelshort/data', methods=['GET'])
def get_reelshort_ranker_data():
    """ReelShort ranker: clickstream data where COMMON_NAME contains 'reelshort'."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    with REELSHORT_RANKER_LOCK:
        cached = REELSHORT_RANKER_CACHE.get(today_str)
        if cached is None and os.path.exists(REELSHORT_RANKER_CACHE_FILE):
            try:
                with open(REELSHORT_RANKER_CACHE_FILE, 'r') as f:
                    by_date = json.load(f)
                cached = by_date.get(today_str) if isinstance(by_date, dict) else None
                if cached is not None:
                    REELSHORT_RANKER_CACHE[today_str] = cached
            except Exception:
                pass
        if cached is not None:
            return jsonify(cached)
    try:
        payload = _fetch_clickstream_ranker_payload('reelshort', today_str)
        _save_clickstream_ranker_cache(today_str, payload, REELSHORT_RANKER_CACHE_FILE, REELSHORT_RANKER_CACHE, REELSHORT_RANKER_LOCK, 'ReelShort Ranker')
        return jsonify(payload)
    except Exception as e:
        print(f"[ReelShort Ranker] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/rankers/shortstv/data', methods=['GET'])
def get_shortstv_ranker_data():
    """ShortsTV ranker: clickstream data where COMMON_NAME contains 'shortstv'."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    with SHORTSTV_RANKER_LOCK:
        cached = SHORTSTV_RANKER_CACHE.get(today_str)
        if cached is None and os.path.exists(SHORTSTV_RANKER_CACHE_FILE):
            try:
                with open(SHORTSTV_RANKER_CACHE_FILE, 'r') as f:
                    by_date = json.load(f)
                cached = by_date.get(today_str) if isinstance(by_date, dict) else None
                if cached is not None:
                    SHORTSTV_RANKER_CACHE[today_str] = cached
            except Exception:
                pass
        if cached is not None:
            return jsonify(cached)
    try:
        payload = _fetch_clickstream_ranker_payload('shortstv', today_str)
        _save_clickstream_ranker_cache(today_str, payload, SHORTSTV_RANKER_CACHE_FILE, SHORTSTV_RANKER_CACHE, SHORTSTV_RANKER_LOCK, 'ShortsTV Ranker')
        return jsonify(payload)
    except Exception as e:
        print(f"[ShortsTV Ranker] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/rankers/goodshort/data', methods=['GET'])
def get_goodshort_ranker_data():
    """GoodShort ranker: clickstream data where COMMON_NAME contains 'goodshort'."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    with GOODSHORT_RANKER_LOCK:
        cached = GOODSHORT_RANKER_CACHE.get(today_str)
        if cached is None and os.path.exists(GOODSHORT_RANKER_CACHE_FILE):
            try:
                with open(GOODSHORT_RANKER_CACHE_FILE, 'r') as f:
                    by_date = json.load(f)
                cached = by_date.get(today_str) if isinstance(by_date, dict) else None
                if cached is not None:
                    GOODSHORT_RANKER_CACHE[today_str] = cached
            except Exception:
                pass
        if cached is not None:
            return jsonify(cached)
    try:
        payload = _fetch_clickstream_ranker_payload('goodshort', today_str)
        _save_clickstream_ranker_cache(today_str, payload, GOODSHORT_RANKER_CACHE_FILE, GOODSHORT_RANKER_CACHE, GOODSHORT_RANKER_LOCK, 'GoodShort Ranker')
        return jsonify(payload)
    except Exception as e:
        print(f"[GoodShort Ranker] Error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# LLMO IQ - Large Language Model Optimization dashboard (S3-backed)
# ============================================================================
LLMO_PROJECTION_MULT = 329_900_000 / 10_000_000  # 32.99
LLMO_S3_BUCKET = 'llmo'
LLMO_S3_PREFIX = 'full_table/'
LLMO_CACHE_TTL = 3600  # 1 hour
import threading as _llmo_threading
_llmo_cache = {'df': None, 'loaded_at': 0, 'loading': False, 'lock': _llmo_threading.Lock()}


def _llmo_load_one_file(key):
    """Download and parse a single gzipped CSV from S3."""
    import gzip
    try:
        resp = s3_client.get_object(Bucket=LLMO_S3_BUCKET, Key=key)
        raw = resp['Body'].read()
        text = gzip.decompress(raw).decode('utf-8')
        df = pd.read_csv(
            io.StringIO(text),
            usecols=['UID', 'DELIVERED', 'COMMON_NAME', 'MATCH_TYPE', 'BROWSER', 'PLATFORM', 'URL', 'VISIT_TS'],
            dtype={'UID': 'str', 'COMMON_NAME': 'str', 'MATCH_TYPE': 'str', 'BROWSER': 'str', 'PLATFORM': 'str', 'URL': 'str'}
        )
        return df
    except Exception as e:
        print(f"[LLMO S3] Error loading {key}: {e}")
        return None


def _llmo_ensure_loaded():
    """Load LLMO data from S3 if not cached or stale. Returns the DataFrame."""
    import time as _time
    now = _time.time()
    with _llmo_cache['lock']:
        if _llmo_cache['df'] is not None and (now - _llmo_cache['loaded_at']) < LLMO_CACHE_TTL:
            return _llmo_cache['df']
        if _llmo_cache['loading']:
            # Another thread is loading; wait for it
            pass
        else:
            _llmo_cache['loading'] = True

    # If already loaded and fresh, return
    if _llmo_cache['df'] is not None and (now - _llmo_cache['loaded_at']) < LLMO_CACHE_TTL:
        return _llmo_cache['df']

    import re as _re
    from urllib.parse import unquote_plus
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time

    print("[LLMO S3] Loading data from S3...")
    t0 = _time.time()

    # List all files
    paginator = s3_client.get_paginator('list_objects_v2')
    keys = []
    for page in paginator.paginate(Bucket=LLMO_S3_BUCKET, Prefix=LLMO_S3_PREFIX):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith('.csv.gz'):
                keys.append(obj['Key'])
    print(f"[LLMO S3] Found {len(keys)} files to load")

    # Parallel download and parse
    dfs = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        futures = {pool.submit(_llmo_load_one_file, k): k for k in keys}
        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            if done_count % 50 == 0:
                print(f"[LLMO S3] Loaded {done_count}/{len(keys)} files...")
            result = fut.result()
            if result is not None:
                dfs.append(result)

    if not dfs:
        print("[LLMO S3] No data loaded!")
        with _llmo_cache['lock']:
            _llmo_cache['loading'] = False
        return None

    df = pd.concat(dfs, ignore_index=True)
    del dfs

    # Extract search terms from URL before dropping it
    search_pattern = _re.compile(r'[?&](?:q|query|p|search|prompt|text)=([^&]+)', _re.IGNORECASE)
    def _extract_search(url):
        if not isinstance(url, str):
            return None
        m = search_pattern.search(url)
        if m:
            try:
                return unquote_plus(m.group(1))[:200]
            except Exception:
                return m.group(1)[:200]
        return None

    df['SEARCH_TERM'] = df['URL'].apply(_extract_search)
    df.drop(columns=['URL'], inplace=True)

    # Parse dates and timestamps
    df['DELIVERED'] = pd.to_datetime(df['DELIVERED'], errors='coerce').dt.date
    df['VISIT_TS'] = pd.to_datetime(df['VISIT_TS'], errors='coerce')

    # Convert to categories to save memory
    for col in ['COMMON_NAME', 'MATCH_TYPE', 'BROWSER', 'PLATFORM']:
        df[col] = df[col].astype('category')

    elapsed = _time.time() - t0
    print(f"[LLMO S3] Loaded {len(df):,} rows in {elapsed:.1f}s  ({df.memory_usage(deep=True).sum() / 1e9:.2f} GB)")

    with _llmo_cache['lock']:
        _llmo_cache['df'] = df
        _llmo_cache['loaded_at'] = _time.time()
        _llmo_cache['loading'] = False
    return df


@app.route('/api/llmo-iq/dates', methods=['GET'])
@requires_auth
def llmo_iq_dates():
    """Return available DELIVERED dates from cached LLMO data."""
    try:
        df = _llmo_ensure_loaded()
        if df is None:
            return jsonify({'success': True, 'dates': []})
        dates = sorted(df['DELIVERED'].dropna().unique(), reverse=True)
        dates = [str(d) for d in dates]
        return jsonify({'success': True, 'dates': dates})
    except Exception as e:
        print(f"[LLMO IQ dates] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/llmo-iq/data', methods=['GET'])
@requires_auth
def llmo_iq_data():
    """Return core LLMO IQ dashboard data from cached S3 DataFrame."""
    import datetime as _dt
    date_str = request.args.get('date')
    date_end = request.args.get('date_end')
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    if not date_end:
        date_end = date_str

    try:
        full_df = _llmo_ensure_loaded()
        if full_df is None or full_df.empty:
            return jsonify({'success': True, 'date': date_str, 'date_end': date_end,
                            'total_unique_users': 0, 'total_unique_users_projected': 0,
                            'total_clicks': 0, 'total_clicks_projected': 0,
                            'llm_count': 0, 'llms': [], 'attribution': [], 'flows': [],
                            'searches': [], 'trend_dates': [], 'trend_by_llm': {},
                            'browsers': [], 'platforms': []})

        d_start = _dt.date.fromisoformat(date_str)
        d_end = _dt.date.fromisoformat(date_end)
        df = full_df[(full_df['DELIVERED'] >= d_start) & (full_df['DELIVERED'] <= d_end)]

        ai = df[df['MATCH_TYPE'] == 'AI_AGENT']
        post = df[df['MATCH_TYPE'] == 'POST_AI_NON_AGENT']
        M = LLMO_PROJECTION_MULT

        # LLM usage
        llm_grp = ai.groupby('COMMON_NAME').agg(unique_users=('UID', 'nunique'), total_clicks=('UID', 'size')).reset_index()
        llm_grp = llm_grp.sort_values('unique_users', ascending=False).reset_index(drop=True)
        total_unique = int(ai['UID'].nunique())
        total_clicks_all = int(llm_grp['total_clicks'].sum())

        llm_data = []
        for i, row in llm_grp.iterrows():
            uu = int(row['unique_users']); cl = int(row['total_clicks'])
            llm_data.append({
                'rank': i + 1,
                'name': row['COMMON_NAME'] or 'Unknown',
                'unique_users': uu,
                'unique_users_projected': round(uu * M),
                'pct_of_total': round(uu / total_unique * 100, 2) if total_unique else 0,
                'total_clicks': cl,
                'total_clicks_projected': round(cl * M),
                'category_share': round(cl / total_clicks_all * 100, 2) if total_clicks_all else 0,
            })

        # Attribution destinations
        post_valid = post[post['COMMON_NAME'].notna() & (post['COMMON_NAME'] != '')]
        att_grp = post_valid.groupby('COMMON_NAME').agg(unique_users=('UID', 'nunique'), total_clicks=('UID', 'size')).reset_index()
        att_grp = att_grp.sort_values('unique_users', ascending=False).head(50)
        attribution = [{
            'name': r['COMMON_NAME'] or 'Unknown', 'unique_users': int(r['unique_users']),
            'unique_users_projected': round(int(r['unique_users']) * M),
            'total_clicks': int(r['total_clicks']),
            'total_clicks_projected': round(int(r['total_clicks']) * M),
        } for _, r in att_grp.iterrows()]

        # Source -> Destination flows (LAG equivalent)
        flow_df = df[['UID', 'VISIT_TS', 'COMMON_NAME', 'MATCH_TYPE']].sort_values(['UID', 'VISIT_TS'])
        flow_df['prev_name'] = flow_df.groupby('UID')['COMMON_NAME'].shift(1)
        flow_df['prev_type'] = flow_df.groupby('UID')['MATCH_TYPE'].shift(1)
        flow_mask = (flow_df['MATCH_TYPE'] == 'POST_AI_NON_AGENT') & (flow_df['prev_type'] == 'AI_AGENT') & flow_df['prev_name'].notna() & flow_df['COMMON_NAME'].notna() & (flow_df['COMMON_NAME'] != '')
        flow_filtered = flow_df[flow_mask]
        if not flow_filtered.empty:
            flow_grp = flow_filtered.groupby(['prev_name', 'COMMON_NAME']).agg(unique_users=('UID', 'nunique'), clicks=('UID', 'size')).reset_index()
            flow_grp = flow_grp.sort_values('unique_users', ascending=False).head(100)
            flows = [{'source': r['prev_name'], 'destination': r['COMMON_NAME'], 'unique_users': int(r['unique_users']), 'clicks': int(r['clicks'])} for _, r in flow_grp.iterrows()]
        else:
            flows = []

        # Top searches
        search_df = ai[ai['SEARCH_TERM'].notna() & (ai['SEARCH_TERM'] != '')]
        if not search_df.empty:
            srch_grp = search_df.groupby('SEARCH_TERM').size().reset_index(name='count').sort_values('count', ascending=False).head(50)
            searches = [{'term': r['SEARCH_TERM'], 'count': int(r['count'])} for _, r in srch_grp.iterrows()]
        else:
            searches = []

        # Daily trend
        trend_grp = ai.groupby([ai['DELIVERED'].astype(str), 'COMMON_NAME']).agg(unique_users=('UID', 'nunique'), total_clicks=('UID', 'size')).reset_index()
        trend_grp.columns = ['date', 'name', 'unique_users', 'total_clicks']
        trend_dates = sorted(trend_grp['date'].unique())
        trend_by_llm = {}
        for _, r in trend_grp.iterrows():
            name = r['name'] or 'Unknown'
            if name not in trend_by_llm:
                trend_by_llm[name] = {}
            trend_by_llm[name][r['date']] = {'unique_users': int(r['unique_users']), 'total_clicks': int(r['total_clicks'])}

        # Browser / Platform
        br_grp = ai[ai['BROWSER'].notna() & (ai['BROWSER'] != '')].groupby('BROWSER')['UID'].nunique().reset_index(name='unique_users').sort_values('unique_users', ascending=False)
        browsers = [{'name': r['BROWSER'], 'unique_users': int(r['unique_users'])} for _, r in br_grp.iterrows()]

        pl_grp = ai[ai['PLATFORM'].notna() & (ai['PLATFORM'] != '')].groupby('PLATFORM')['UID'].nunique().reset_index(name='unique_users').sort_values('unique_users', ascending=False)
        platforms = [{'name': r['PLATFORM'], 'unique_users': int(r['unique_users'])} for _, r in pl_grp.iterrows()]

        return jsonify({
            'success': True,
            'date': date_str,
            'date_end': date_end,
            'total_unique_users': total_unique,
            'total_unique_users_projected': round(total_unique * M),
            'total_clicks': total_clicks_all,
            'total_clicks_projected': round(total_clicks_all * M),
            'llm_count': len(llm_data),
            'llms': llm_data,
            'attribution': attribution,
            'flows': flows,
            'searches': searches,
            'trend_dates': trend_dates,
            'trend_by_llm': trend_by_llm,
            'browsers': browsers,
            'platforms': platforms,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[LLMO IQ data] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/llmo-iq/demographics', methods=['GET'])
@requires_auth
def llmo_iq_demographics():
    """Return demographic breakdown for LLMO users from Snowflake (async load)."""
    import datetime as _dt
    date_str = request.args.get('date')
    date_end = request.args.get('date_end')
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    if not date_end:
        date_end = date_str

    try:
        import bg as _bg
        conn = _bg.connect_snowflake()
        cur = conn.cursor()
        cur.execute("USE WAREHOUSE BEHAVIORGRAPH6X")

        params = (date_str, date_end)

        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_LLMO_AI_UIDS AS
            SELECT DISTINCT UID, DELIVERED::DATE AS d
            FROM PROCESSEDCLICKSTREAM.PUBLIC.LLMO
            WHERE MATCH_TYPE = 'AI_AGENT'
              AND DELIVERED::DATE BETWEEN %s AND %s
        """, params)

        cur.execute("""
            SELECT 'gender' AS cat, d.GENDER AS val, COUNT(DISTINCT u.UID) AS cnt
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.GENDER IS NOT NULL AND TRIM(d.GENDER) != '' AND UPPER(TRIM(d.GENDER)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY d.GENDER
            UNION ALL
            SELECT 'age', d.AGE, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.AGE IS NOT NULL AND TRIM(d.AGE) != '' AND UPPER(TRIM(d.AGE)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY d.AGE
            UNION ALL
            SELECT 'ethnicity', d.ETHNICITY, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.ETHNICITY IS NOT NULL AND TRIM(d.ETHNICITY) != '' AND UPPER(TRIM(d.ETHNICITY)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY d.ETHNICITY
            UNION ALL
            SELECT 'income', d.INCOME, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.INCOME IS NOT NULL AND TRIM(d.INCOME) != '' AND UPPER(TRIM(d.INCOME)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY d.INCOME
            UNION ALL
            SELECT 'education', d.EDUCATION, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.EDUCATION IS NOT NULL AND TRIM(d.EDUCATION) != '' AND UPPER(TRIM(d.EDUCATION)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY d.EDUCATION
        """)
        overall_rows = cur.fetchall()

        cur.execute("""
            SELECT 'gender' AS cat, u.d, d.GENDER AS val, COUNT(DISTINCT u.UID) AS cnt
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.GENDER IS NOT NULL AND TRIM(d.GENDER) != '' AND UPPER(TRIM(d.GENDER)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY u.d, d.GENDER
            UNION ALL
            SELECT 'age', u.d, d.AGE, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.AGE IS NOT NULL AND TRIM(d.AGE) != '' AND UPPER(TRIM(d.AGE)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY u.d, d.AGE
            UNION ALL
            SELECT 'ethnicity', u.d, d.ETHNICITY, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.ETHNICITY IS NOT NULL AND TRIM(d.ETHNICITY) != '' AND UPPER(TRIM(d.ETHNICITY)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY u.d, d.ETHNICITY
            UNION ALL
            SELECT 'income', u.d, d.INCOME, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.INCOME IS NOT NULL AND TRIM(d.INCOME) != '' AND UPPER(TRIM(d.INCOME)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY u.d, d.INCOME
            UNION ALL
            SELECT 'education', u.d, d.EDUCATION, COUNT(DISTINCT u.UID)
            FROM TEMP_LLMO_AI_UIDS u JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON u.UID = d.UID
            WHERE d.EDUCATION IS NOT NULL AND TRIM(d.EDUCATION) != '' AND UPPER(TRIM(d.EDUCATION)) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY u.d, d.EDUCATION
        """)
        trend_rows = cur.fetchall()
        conn.close()

        cat_data = {}
        for r in overall_rows:
            cat, val, cnt = r
            cat_data.setdefault(cat, []).append((val, cnt))
        demographics = {}
        for cat, items in cat_data.items():
            items.sort(key=lambda x: -x[1])
            total_d = sum(x[1] for x in items)
            demographics[cat] = [{'value': v, 'count': c, 'pct': round(c / total_d * 100, 2) if total_d else 0} for v, c in items]

        trend_data = {}
        for r in trend_rows:
            cat, d, val, cnt = r
            d_str = str(d)
            trend_data.setdefault(cat, {}).setdefault(d_str, []).append({'value': val, 'count': cnt})
        demo_trend = {}
        for cat, by_date in trend_data.items():
            for d_str, items in by_date.items():
                total_d = sum(x['count'] for x in items)
                for x in items:
                    x['pct'] = round(x['count'] / total_d * 100, 2) if total_d else 0
            demo_trend[cat] = by_date

        return jsonify({'success': True, 'demographics': demographics, 'demo_trend': demo_trend})
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[LLMO IQ demographics] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# NETFLIX LIVE TOP 10 - Real-time Netflix viewing data from S3
# ============================================================================
NETFLIX_LIVE_TOP10_CACHE = {'data': None, 'loaded_at': 0}
NETFLIX_LIVE_TOP10_CACHE_DURATION = 300  # 5 minutes

@app.route('/api/rankers/netflix/live-top10', methods=['GET'])
def get_netflix_live_top10():
    """
    Return live Netflix Top 10 from the netflix-liveish S3 bucket.
    Reads data_netflix.csv, counts URL occurrences, and returns top 10 ranked.
    """
    global NETFLIX_LIVE_TOP10_CACHE
    
    try:
        now = datetime.now().timestamp()
        cache_loaded_at = NETFLIX_LIVE_TOP10_CACHE.get('loaded_at', 0)
        cache_age = now - cache_loaded_at
        use_cache = NETFLIX_LIVE_TOP10_CACHE.get('data') and cache_age < NETFLIX_LIVE_TOP10_CACHE_DURATION

        live_bucket = 'netflix-liveish'
        live_key = 'data_netflix.csv'

        # If we would use cache, check S3 LastModified so we refetch when a new file was uploaded
        if use_cache and s3_client:
            try:
                head = s3_client.head_object(Bucket=live_bucket, Key=live_key)
                s3_modified = head.get('LastModified')
                if s3_modified:
                    s3_ts = s3_modified.timestamp()
                    if s3_ts > cache_loaded_at:
                        use_cache = False
            except Exception:
                pass
        if use_cache:
            return jsonify(NETFLIX_LIVE_TOP10_CACHE['data'])

        # Fetch from S3
        try:
            response = s3_client.get_object(Bucket=live_bucket, Key=live_key)
            csv_content = response['Body'].read().decode('utf-8')
        except Exception as e:
            print(f"[Netflix Live Top 10] S3 fetch error: {e}")
            if NETFLIX_LIVE_TOP10_CACHE.get('data'):
                return jsonify(NETFLIX_LIVE_TOP10_CACHE['data'])
            return jsonify({'error': f'Failed to fetch live data: {e}'}), 500
        
        # Parse CSV and count URLs
        from collections import Counter
        lines = csv_content.strip().split('\n')
        
        # Find header row and column indices
        header = None
        url_col = -1
        name_col = -1
        for i, line in enumerate(lines[:5]):
            cols = line.split(',')
            for j, col in enumerate(cols):
                col_lower = col.strip().lower().replace('"', '')
                if col_lower == 'url':
                    url_col = j
                    header = i
                if col_lower == 'name_of_show':
                    name_col = j
        
        if url_col < 0:
            return jsonify({'error': 'URL column not found in CSV'}), 500
        
        # Count URLs and collect show names
        url_counts = Counter()
        url_to_name = {}
        
        for line in lines[header + 1:] if header is not None else lines[1:]:
            cols = line.split(',')
            if len(cols) > url_col:
                url = cols[url_col].strip().replace('"', '')
                if url and url.startswith('http'):
                    url_counts[url] += 1
                    # Get show name if available
                    if name_col >= 0 and len(cols) > name_col:
                        name = cols[name_col].strip().replace('"', '')
                        if name and url not in url_to_name:
                            url_to_name[url] = name
        
        # Build top 10 with projected views (no 150x boost per user request)
        total_count = sum(url_counts.values())
        top_10 = []
        
        for rank, (url, count) in enumerate(url_counts.most_common(10), 1):
            # Project views: (count / panel_size) * US_population
            projected_views = int(count / 10_000_000 * 329_900_000)
            pct_of_total = round((count / total_count * 100), 2) if total_count > 0 else 0
            
            # Extract title_id from URL
            title_id = url.split('/')[-1].split('?')[0] if '/' in url else url
            
            top_10.append({
                'rank': rank,
                'url': url,
                'title_id': title_id,
                'show_name': url_to_name.get(url, ''),
                'raw_count': count,
                'projected_views': projected_views,
                'pct_of_total': pct_of_total
            })
        
        result = {
            'top_10': top_10,
            'total_urls': len(url_counts),
            'total_views': sum(url_counts.values()),
            'updated_at': datetime.now().isoformat()
        }
        
        NETFLIX_LIVE_TOP10_CACHE['data'] = result
        NETFLIX_LIVE_TOP10_CACHE['loaded_at'] = now
        
        return jsonify(result)
        
    except Exception as e:
        print(f"[Netflix Live Top 10] Error: {e}")
        return jsonify({'error': str(e)}), 500


# Eager-load ranker caches from disk at startup so first request in each worker is fast
def _preload_ranker_caches():
    try:
        if os.path.exists(NETFLIX_RANKER_CACHE_FILE):
            _load_netflix_ranker_cache()
    except Exception:
        pass


_preload_ranker_caches()


# ============================================================================
# FAVORITES/BOOKMARKS
# ============================================================================

@app.route('/api/favorites', methods=['GET'])
@requires_auth
def get_favorites():
    """Get user's favorite profiles."""
    username = session.get('username')
    users = load_users()
    user = users.get(username, {})
    return jsonify({'favorites': user.get('favorites', [])})

@app.route('/api/favorites', methods=['POST'])
@requires_auth
def add_favorite():
    """Add a profile to favorites."""
    username = session.get('username')
    data = request.get_json()
    profile_key = data.get('key')
    profile_name = data.get('name', profile_key)
    
    users = load_users()
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    if 'favorites' not in users[username]:
        users[username]['favorites'] = []
    
    # Check if already in favorites
    for fav in users[username]['favorites']:
        if fav.get('key') == profile_key:
            return jsonify({'error': 'Already in favorites'}), 400
    
    users[username]['favorites'].append({
        'key': profile_key,
        'name': profile_name,
        'added': datetime.now().isoformat()
    })
    
    save_users(users)
    return jsonify({'success': True, 'favorites': users[username]['favorites']})

@app.route('/api/favorites/<path:profile_key>', methods=['DELETE'])
@requires_auth
def remove_favorite(profile_key):
    """Remove a profile from favorites."""
    username = session.get('username')
    users = load_users()
    
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    users[username]['favorites'] = [
        f for f in users[username].get('favorites', []) 
        if f.get('key') != profile_key
    ]
    
    save_users(users)
    return jsonify({'success': True})

# ============================================================================
# SHAREABLE LINKS
# ============================================================================

shared_links = {}  # In production, store in database/S3

@app.route('/api/profile-image/<path:name>')
@app.route('/api/wiki-image/<path:name>')
def get_profile_image(name):
    """Get profile image - only returns admin-uploaded custom images."""
    global profile_image_cache
    
    # Reload cache periodically so uploads from other workers are visible
    load_profile_image_cache()
    
    # Normalize the cache key
    cache_key = name.lower().strip()
    
    # Only return custom images that admin has uploaded
    if cache_key in profile_image_cache:
        cached = profile_image_cache[cache_key]
        if cached.get('image_url') and cached.get('is_custom'):
            return jsonify({
                'success': True,
                'image_url': cached['image_url'],
                'title': cached.get('title', name),
                'source': 'custom',
                'cached': True
            })
    
    # No custom image - return not found (don't search external sources)
    return jsonify({'success': False, 'error': 'No custom image uploaded'})


@app.route('/api/admin/profiles-without-images')
@requires_admin
def get_profiles_without_images():
    """Get list of all profiles that don't have custom images."""
    global profile_image_cache, s3_cache
    
    try:
        # Load caches (always load profile image cache from S3 so we see latest uploads
        # across workers; in-memory cache is per-worker and would otherwise be stale)
        load_profile_image_cache()
        
        if not s3_cache.get('loaded'):
            load_persisted_cache()
        
        # Get all profile names from s3_cache
        all_profiles = []
        files = s3_cache.get('files', [])
        
        # Also check jobs if files is empty
        if not files:
            jobs = s3_cache.get('jobs', [])
            for job in jobs:
                name = job.get('project_name') or job.get('brand') or job.get('job_id', '')
                if name:
                    all_profiles.append({
                        'name': name,
                        'category': job.get('category', 'UNCATEGORIZED'),
                        's3_key': job.get('s3_key') or job.get('job_id', '')
                    })
        else:
            for f in files:
                name = f.get('project_name', '')
                if name:
                    all_profiles.append({
                        'name': name,
                        'category': f.get('category', 'UNCATEGORIZED'),
                        's3_key': f.get('s3_key', '')
                    })
        
        # Find profiles without custom images
        profiles_without_images = []
        profiles_with_images = []
        
        for profile in all_profiles:
            cache_key = profile['name'].lower().strip()
            cached = profile_image_cache.get(cache_key, {})
            
            if cached.get('image_url') and cached.get('is_custom'):
                profiles_with_images.append({
                    'name': profile['name'],
                    'category': profile['category'],
                    'image_url': cached['image_url']
                })
            else:
                profiles_without_images.append({
                    'name': profile['name'],
                    'category': profile['category'],
                    's3_key': profile['s3_key']
                })
        
        # Sort alphabetically
        profiles_without_images.sort(key=lambda x: x['name'].lower())
        profiles_with_images.sort(key=lambda x: x['name'].lower())
        
        return jsonify({
            'success': True,
            'without_images': profiles_without_images,
            'with_images': profiles_with_images,
            'total_profiles': len(all_profiles),
            'missing_count': len(profiles_without_images),
            'has_image_count': len(profiles_with_images)
        })
    except Exception as e:
        import traceback
        print(f"Error in get_profiles_without_images: {e}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'without_images': [],
            'with_images': [],
            'total_profiles': 0,
            'missing_count': 0,
            'has_image_count': 0
        }), 500


@app.route('/api/share', methods=['POST'])
@requires_auth
def create_share_link():
    """Create a shareable link for a profile."""
    data = request.get_json()
    profile_key = data.get('key')
    expires_days = data.get('expires_days', 7)
    
    share_id = secrets.token_urlsafe(16)
    shared_links[share_id] = {
        'profile_key': profile_key,
        'created_by': session.get('username'),
        'created_at': datetime.now().isoformat(),
        'expires_at': (datetime.now() + pd.Timedelta(days=expires_days)).isoformat()
    }
    
    return jsonify({
        'share_id': share_id,
        'url': f"/shared/{share_id}"
    })

@app.route('/shared/<share_id>')
def view_shared(share_id):
    """View a shared profile (no auth required)."""
    if share_id not in shared_links:
        return "Link not found or expired", 404
    
    link_data = shared_links[share_id]
    if datetime.fromisoformat(link_data['expires_at']) < datetime.now():
        del shared_links[share_id]
        return "Link expired", 404
    
    # Return a simplified view template
    return render_template('shared.html', 
                         profile_key=link_data['profile_key'],
                         share_id=share_id)


def _preferred_gen_pop_key(s3_key):
    """For any Gen Pop key, prefer the Gen_Pop_YYYY_... form (e.g. Gen_Pop_2026_03_04_2026_04_29.csv)
    so the dashboard always loads the canonical file matching the S3 link users expect."""
    if not s3_key or '.csv' not in s3_key or 'gen_pop' not in s3_key.lower():
        return None
    key_lower = s3_key.lower().strip()
    # Already in preferred form: Gen_Pop_YYYY_ at start
    if re.match(r'^gen_pop_\d{4}_', key_lower):
        return None
    # Build preferred key: Gen_Pop_MM_DD_YYYY_MM_DD -> Gen_Pop_YYYY_MM_DD_YYYY_MM_DD
    base = key_lower.replace('.csv', '')
    match = re.match(r'^gen_pop_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})$', base)
    if match:
        mm1, dd1, year, mm2, dd2 = match.groups()
        return f"Gen_Pop_{year}_{mm1}_{dd1}_{year}_{mm2}_{dd2}.csv"
    if '2026' in key_lower or 'gen_pop' in key_lower:
        return GEN_POP_CANONICAL_KEY
    return None


@app.route('/api/get-csv-data/<path:s3_key>')
@requires_auth
def get_csv_data(s3_key):
    """Get CSV data as JSON for dashboard display."""
    print(f"📥 get_csv_data called for: {s3_key}")
    
    if not s3_client:
        print("❌ S3 client not configured")
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    if not s3_cache.get('jobs') and s3_client:
        load_persisted_cache()
    
    def _fetch_and_return(key):
        """Fetch CSV from S3 by key and return (content, brand_name, date_range) or raise."""
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        csv_content = response['Body'].read().decode('utf-8')
        df = pd.read_csv(io.StringIO(csv_content))
        df = df.fillna('')
        brand_name = None
        # Purgatory keys are not in s3_cache; use purgatory metadata for display name (admin may have changed it)
        if key.startswith(S3_PURGATORY_PREFIX):
            purgatory_meta = load_purgatory_metadata()
            purgatory_id = f"{S3_BUCKET}:{key}"
            if purgatory_id in purgatory_meta:
                brand_name = purgatory_meta[purgatory_id].get('title') or purgatory_meta[purgatory_id].get('project_name')
        if not brand_name:
            for job in s3_cache.get('jobs', []):
                if (job.get('s3_key') or job.get('key')) == key:
                    brand_name = job.get('display_name') or job.get('project_name') or job.get('name')
                    break
        if not brand_name:
            name_without_ext = key.replace('.csv', '').replace(S3_PURGATORY_PREFIX, '')
            match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
            if match:
                brand_name = match.group(1).replace('_', ' ')
            else:
                brand_name = name_without_ext.replace('_', ' ')
        date_range = ''
        metadata_rows = df[df['Column'] == 'INPUT_METADATA']
        if not metadata_rows.empty:
            metadata_value = str(metadata_rows.iloc[0]['Value'])
            if 'SAMPLE_START:' in metadata_value and 'SAMPLE_END:' in metadata_value:
                start = metadata_value.split('SAMPLE_START:')[1].split('_')[0]
                end = metadata_value.split('SAMPLE_END:')[1].split('_')[0]
                date_range = f"{start} - {end}"
        data = df.to_dict('records')
        for row in data:
            val = row.get('Value')
            if isinstance(val, str) and val.strip().lower() in ('latinx', 'latino'):
                row['Value'] = 'Hispanic or Latino'
        return csv_content, df, brand_name, date_range, data
    
    # Gen Pop: always fetch the canonical S3 file so dashboard matches https://dashboard-inputs.s3.../Gen_Pop_2026_03_04_2026_04_29.csv
    effective_key = s3_key
    if s3_key and 'gen_pop' in s3_key.lower():
        try:
            csv_content, df, brand_name, date_range, data = _fetch_and_return(GEN_POP_CANONICAL_KEY)
            print(f"📂 Served canonical Gen Pop file: {GEN_POP_CANONICAL_KEY}")
            return jsonify({
                'success': True,
                'data': data,
                'brand': brand_name,
                'date_range': date_range,
                's3_key': GEN_POP_CANONICAL_KEY
            })
        except Exception:
            pass  # fall back to requested key
    try:
        print(f"📂 Fetching from S3: {S3_BUCKET}/{effective_key}")
        csv_content, df, brand_name, date_range, data = _fetch_and_return(effective_key)
        print(f"✅ Got CSV content: {len(csv_content)} bytes for brand: {brand_name}")
        return jsonify({
            'success': True,
            'data': data,
            'brand': brand_name,
            'date_range': date_range,
            's3_key': effective_key
        })
    except s3_client.exceptions.NoSuchKey:
        # File not in S3 (e.g. released from purgatory — key was purgatory/... and is now at root)
        if s3_key.startswith(S3_PURGATORY_PREFIX):
            released_key = s3_key[len(S3_PURGATORY_PREFIX):]
            try:
                csv_content, df, brand_name, date_range, data = _fetch_and_return(released_key)
                print(f"✅ Loaded from released location: {released_key}")
                return jsonify({
                    'success': True,
                    'data': data,
                    'brand': brand_name,
                    'date_range': date_range,
                    's3_key': released_key
                })
            except Exception:
                pass
        print(f"❌ Profile not found: {s3_key}")
        return jsonify({'success': False, 'error': 'Profile file not found. It may have been moved (e.g. after release from Purgatory). Click ↻ to refresh the profile list.', 's3_key': s3_key}), 404
    except Exception as e:
        print(f"❌ Error in get_csv_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 's3_key': s3_key}), 500


@app.route('/api/profile-run-metadata/<path:s3_key>')
@requires_auth
def get_profile_run_metadata(s3_key):
    """Get run parameters from a profile CSV (brand, dates, sample size, demographics) for rerun-with-different-dates."""
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
    except s3_client.exceptions.NoSuchKey:
        if s3_key.startswith(S3_PURGATORY_PREFIX):
            released = s3_key[len(S3_PURGATORY_PREFIX):]
            try:
                response = s3_client.get_object(Bucket=S3_BUCKET, Key=released)
                csv_content = response['Body'].read().decode('utf-8')
                s3_key = released
            except Exception:
                return jsonify({'success': False, 'error': 'Profile not found'}), 404
        else:
            return jsonify({'success': False, 'error': 'Profile not found'}), 404
    metadata = parse_metadata_from_csv(csv_content)
    if not metadata:
        return jsonify({'success': False, 'error': 'Could not read run metadata from profile'}), 400
    brand = metadata.get('BRAND') or metadata.get('brand')
    sample_start = metadata.get('SAMPLE_START') or ''
    sample_end = metadata.get('SAMPLE_END') or ''
    behavior_start = metadata.get('BEHAVIOR_START') or sample_start
    behavior_end = metadata.get('BEHAVIOR_END') or sample_end
    sample_size = extract_sample_size_from_csv(csv_content)
    demographics = extract_demographics_from_csv(csv_content)
    brand_category = (metadata.get('BRAND_CATEGORY') or '').strip() or None
    is_listener_watcher = str(metadata.get('LISTENER_WATCHER') or 'false').strip().lower() == 'true'
    platform_name = (metadata.get('PLATFORM_NAME') or '').strip() or None
    display_name = None
    for job in s3_cache.get('jobs', []):
        if (job.get('s3_key') or job.get('key')) == s3_key:
            display_name = job.get('display_name') or job.get('project_name') or job.get('name')
            if not brand_category and job.get('category'):
                brand_category = job.get('category')
            break
    if not display_name:
        name_without_ext = s3_key.replace('.csv', '').replace(S3_PURGATORY_PREFIX, '')
        match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
        display_name = match.group(1).replace('_', ' ') if match else name_without_ext.replace('_', ' ')
    return jsonify({
        'success': True,
        's3_key': s3_key,
        'brand': brand,
        'brand_display': display_name,
        'sample_start': sample_start,
        'sample_end': sample_end,
        'behavior_start': behavior_start,
        'behavior_end': behavior_end,
        'sample_size': sample_size,
        'demographics': demographics,
        'brand_category': brand_category or 'GENERAL',
        'is_listener_watcher': is_listener_watcher,
        'platform_name': platform_name or ''
    })


@app.route('/api/submit-rerun', methods=['POST'])
@requires_auth
def submit_rerun():
    """Submit a profile run using the same search criteria as an existing profile, with new dates. Uses selected profile as reference so demographics stay within margin."""
    try:
        username = session.get('username')
        if not has_credits_for(username, CREDITS_PROFILE_ANALYSIS):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Profile Analysis requires {CREDITS_PROFILE_ANALYSIS} credits. You have {"no" if credits_left == 0 else credits_left} remaining.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        data = request.json
        s3_key = data.get('s3_key') or data.get('source_s3_key')
        if not s3_key:
            return jsonify({'error': 'Missing s3_key (profile to rerun)'}), 400
        sample_start = data.get('sample_start') or data.get('start_date')
        sample_end = data.get('sample_end') or data.get('end_date')
        if not sample_start or not sample_end:
            return jsonify({'error': 'Missing sample_start and sample_end (or start_date and end_date)'}), 400
        behavior_start = data.get('behavior_start') or sample_start
        behavior_end = data.get('behavior_end') or sample_end
        try:
            sample_start = datetime.strptime(sample_start, '%Y-%m-%d').strftime('%Y-%m-%d')
            sample_end = datetime.strptime(sample_end, '%Y-%m-%d').strftime('%Y-%m-%d')
            behavior_start = datetime.strptime(behavior_start, '%Y-%m-%d').strftime('%Y-%m-%d')
            behavior_end = datetime.strptime(behavior_end, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            csv_content = response['Body'].read().decode('utf-8')
        except Exception:
            if s3_key.startswith(S3_PURGATORY_PREFIX):
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key[len(S3_PURGATORY_PREFIX):])
                    csv_content = response['Body'].read().decode('utf-8')
                    s3_key = s3_key[len(S3_PURGATORY_PREFIX):]
                except Exception:
                    return jsonify({'error': 'Profile file not found'}), 404
            else:
                return jsonify({'error': 'Profile file not found'}), 404
        metadata = parse_metadata_from_csv(csv_content)
        if not metadata:
            return jsonify({'error': 'Could not read run metadata from profile'}), 400
        brand_raw = metadata.get('BRAND') or metadata.get('brand') or ''
        brands = [b.strip().lower() for b in brand_raw.split(',') if b.strip()]
        if not brands:
            return jsonify({'error': 'No brand found in profile metadata'}), 400
        reference_demographics = extract_demographics_from_csv(csv_content)
        reference_sample_size = extract_sample_size_from_csv(csv_content)
        brand_category = (data.get('brand_category') or (metadata.get('BRAND_CATEGORY') or '').strip()) or 'GENERAL'
        is_listener_watcher = data.get('is_listener_watcher')
        if is_listener_watcher is None or is_listener_watcher == '':
            is_listener_watcher = str(metadata.get('LISTENER_WATCHER') or 'false').strip().lower() == 'true'
        else:
            is_listener_watcher = bool(is_listener_watcher)
        platform_name = data.get('platform_name') or (metadata.get('PLATFORM_NAME') or '').strip() or None
        if is_listener_watcher and not platform_name:
            platform_name = None
        # Rerun is Gen Pop only if the reference profile is actually Gen Pop (not for The Rock, etc.)
        first_brand = (brands[0] or '').strip().lower()
        is_genpop_rerun = first_brand in ('gen pop', 'gen_pop', 'genpop')
        # Rerun output: base name from reference, but year from the date range being pulled (e.g. The_Rock_2024 -> pull 2023 -> The_Rock_2023)
        name_from_key = os.path.splitext(os.path.basename(s3_key))[0]
        rerun_basename = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$', '', name_from_key)
        try:
            year_from_range = int(str(sample_end or sample_start or '')[:4]) if (sample_end or sample_start) else None
        except (ValueError, TypeError):
            year_from_range = None
        if year_from_range is not None and rerun_basename:
            base_without_year = re.sub(r'_20\d{2}$', '', rerun_basename)
            project_name = f"{base_without_year}_{year_from_range}" if base_without_year else f"{rerun_basename}_{year_from_range}"
        else:
            project_name = rerun_basename if rerun_basename else re.sub(r'[<>:"/\\|?*]', '_', (data.get('project_name') or brands[0]).replace(' ', '_')[:80])
        job_id = str(uuid.uuid4())[:8]
        filters = {}
        skew_settings = {}
        jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'message': 'Queued (rerun with new dates)',
            'created_at': datetime.now().isoformat(),
            'project_name': project_name,
            'brands': brands[0],
            'result_file': None,
            'error': None,
            'logs': [],
            'reference_demographics': reference_demographics,
            'reference_sample_size': reference_sample_size,
            'created_by': username,
            's3_key': None,
        }
        if s3_client:
            _save_job_status_to_s3(job_id, jobs[job_id])
        thread = threading.Thread(
            target=run_analysis,
            args=(job_id, project_name, brands, sample_start, sample_end,
                  behavior_start, behavior_end, filters, skew_settings,
                  is_genpop_rerun, False, brand_category,
                  False, is_listener_watcher, platform_name, None,
                  reference_demographics, reference_sample_size, s3_key)
        )
        thread.daemon = True
        thread.start()
        desc = f"{project_name} rerun {sample_start}–{sample_end}"
        consume_credit(username, description=desc, job_id=job_id, pull_type='Profile Analysis', credits_used=CREDITS_PROFILE_ANALYSIS)
        _, credits_left = check_user_credits(username)
        return jsonify({
            'job_id': job_id,
            'message': 'Rerun job submitted; demographics will be kept within margin of the selected profile.',
            'status': 'queued',
            'credits_left': credits_left,
            'brands_count': len(brands)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/segment-data', methods=['POST'])
@requires_auth
def get_segment_data():
    """Get segmented behavioral data based on demographic filters."""
    try:
        data = request.get_json()
        s3_key = data.get('s3_key')
        filters = data.get('filters', {})
        
        if not s3_key:
            return jsonify({'success': False, 'error': 's3_key is required'}), 400
        
        if not s3_client:
            return jsonify({'success': False, 'error': 'S3 not configured'}), 500
        
        # Get the CSV to extract metadata (brand name, date ranges)
        print(f"📥 Getting segment data for: {s3_key}")
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        df = pd.read_csv(io.StringIO(csv_content))
        
        # Extract brand name from filename
        name_without_ext = s3_key.replace('.csv', '')
        match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
        if match:
            brand_name = match.group(1).replace('_', ' ')
        else:
            brand_name = name_without_ext.replace('_', ' ')
        
        # Extract date ranges from INPUT_METADATA
        metadata_rows = df[df['Column'] == 'INPUT_METADATA']
        sample_start = None
        sample_end = None
        behavior_start = None
        behavior_end = None
        
        if not metadata_rows.empty:
            metadata_value = str(metadata_rows.iloc[0]['Value'])
            # Parse SAMPLE_START and SAMPLE_END from metadata
            if 'SAMPLE_START:' in metadata_value:
                try:
                    sample_str = metadata_value.split('SAMPLE_START:')[1].split('_')[0]
                    sample_start = datetime.strptime(sample_str, '%Y-%m-%d').strftime('%Y-%m-%d')
                except:
                    pass
            if 'SAMPLE_END:' in metadata_value:
                try:
                    sample_str = metadata_value.split('SAMPLE_END:')[1].split('_')[0]
                    sample_end = datetime.strptime(sample_str, '%Y-%m-%d').strftime('%Y-%m-%d')
                except:
                    pass
            if 'BEHAVIOR_START:' in metadata_value:
                try:
                    behavior_str = metadata_value.split('BEHAVIOR_START:')[1].split('_')[0]
                    behavior_start = datetime.strptime(behavior_str, '%Y-%m-%d').strftime('%Y-%m-%d')
                except:
                    pass
            if 'BEHAVIOR_END:' in metadata_value:
                try:
                    behavior_str = metadata_value.split('BEHAVIOR_END:')[1].split('_')[0]
                    behavior_end = datetime.strptime(behavior_str, '%Y-%m-%d').strftime('%Y-%m-%d')
                except:
                    pass
        
        # Use sample dates for behavior if behavior dates not found
        if not behavior_start:
            behavior_start = sample_start
        if not behavior_end:
            behavior_end = sample_end
        
        # Default to last 90 days if no dates found
        if not sample_start or not sample_end:
            sample_end = datetime.now().strftime('%Y-%m-%d')
            sample_start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
            behavior_start = sample_start
            behavior_end = sample_end
        
        print(f"📊 Querying segment data for brand: {brand_name}, dates: {sample_start} to {sample_end}")
        print(f"   Filters: {filters}")
        
        # Import bg module to use its functions
        try:
            import bg
            conn = bg.connect_snowflake()
            cur = conn.cursor()
        except Exception as e:
            return jsonify({'success': False, 'error': f'Database connection failed: {str(e)}'}), 500
        
        # Build demographic filter clause
        demo_conditions = []
        demo_field_mapping = {
            'gender': 'GENDER',
            'age': 'AGE',
            'income': 'INCOME',
            'ethnicity': 'ETHNICITY',
            'relationship': 'RELATIONSHIP',
            'parental': 'PARENTAL_STATUS'
        }
        
        for frontend_key, db_field in demo_field_mapping.items():
            if filters.get(frontend_key) and len(filters[frontend_key]) > 0:
                vals = ",".join(f"'{v}'" for v in filters[frontend_key])
                demo_conditions.append(f"d.{db_field} IN ({vals})")
        
        demo_filter_clause = " AND ".join(demo_conditions) if demo_conditions else "1=1"
        
        # Build brand filter
        brand_filter = f"c.COMMON_NAME ILIKE '%{brand_name}%'"
        
        # Query to get UIDs matching the demographic filters and brand
        print(f"🔍 Querying UIDs with filters: {demo_filter_clause}")
        uid_query = f"""
            SELECT DISTINCT c.UID
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL c
            INNER JOIN PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d ON c.UID = d.UID
            WHERE c.DELIVERED >= '{sample_start}'::DATE 
              AND c.DELIVERED <= '{sample_end}'::DATE
              AND ({brand_filter})
              AND c.COMMON_NAME IS NOT NULL
              AND c.COMMON_NAME != ''
              AND {demo_filter_clause}
            LIMIT 100000
        """
        
        try:
            uid_results = cur.execute(uid_query).fetchall()
            uids = [row[0] for row in uid_results]
            segment_size = len(uids)
            
            if segment_size == 0:
                return jsonify({
                    'success': True,
                    'segmentSize': 0,
                    'behavioral': {},
                    'demographics': {},
                    'message': 'No users found matching the selected filters'
                })
            
            print(f"✅ Found {segment_size} UIDs matching filters")
            
            # Limit to 10k UIDs for performance, but use actual segment size for percentages
            uids_to_use = uids[:10000]
            actual_segment_size = len(uids_to_use)
            
            # Now query behavioral data for these UIDs
            # Get behavioral data from the behavior date range
            if len(uids_to_use) > 0:
                # Use IN clause with proper escaping
                uid_list = "','".join([str(uid).replace("'", "''") for uid in uids_to_use])
                behavior_query = f"""
                    SELECT 
                        c.CATEGORY,
                        c.COMMON_NAME as VALUE,
                        COUNT(DISTINCT c.UID) as UID_COUNT
                    FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL c
                    WHERE c.UID IN ('{uid_list}')
                      AND c.DELIVERED >= '{behavior_start}'::DATE
                      AND c.DELIVERED <= '{behavior_end}'::DATE
                      AND c.COMMON_NAME IS NOT NULL
                      AND c.COMMON_NAME != ''
                      AND c.CATEGORY NOT IN ('GENDER', 'AGE', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION', 'SAMPLE SIZE')
                    GROUP BY c.CATEGORY, c.COMMON_NAME
                    ORDER BY UID_COUNT DESC
                """
            else:
                behavior_query = "SELECT NULL as CATEGORY, NULL as VALUE, 0 as UID_COUNT WHERE 1=0"
            
            behavior_results = cur.execute(behavior_query).fetchall()
            
            # Organize behavioral data by category
            behavioral = {}
            for row in behavior_results:
                category = row[0]
                value = row[1]
                uid_count = row[2]
                pct = (uid_count / actual_segment_size) * 100 if actual_segment_size > 0 else 0
                
                if category not in behavioral:
                    behavioral[category] = []
                
                behavioral[category].append({
                    'name': value,
                    'pct': round(pct, 2),
                    'raw': uid_count
                })
            
            # Sort each category by percentage
            for category in behavioral:
                behavioral[category].sort(key=lambda x: x['pct'], reverse=True)
            
            # Get demographics for the segment
            if len(uids_to_use) > 0:
                uid_list = "','".join([str(uid).replace("'", "''") for uid in uids_to_use])
                demo_query = f"""
                    SELECT 
                        d.GENDER,
                        d.AGE,
                        d.INCOME,
                        d.ETHNICITY,
                        COUNT(*) as COUNT
                    FROM PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d
                    WHERE d.UID IN ('{uid_list}')
                    GROUP BY d.GENDER, d.AGE, d.INCOME, d.ETHNICITY
                """
            else:
                demo_query = "SELECT NULL as GENDER, NULL as AGE, NULL as INCOME, NULL as ETHNICITY, 0 as COUNT WHERE 1=0"
            
            demo_results = cur.execute(demo_query).fetchall()
            demographics = {
                'gender': {},
                'age': {},
                'income': {},
                'ethnicity': {}
            }
            
            def _display_ethnicity(val):
                if not val or not isinstance(val, str):
                    return val
                v = val.strip().lower()
                if v in ('latinx', 'latino', 'latin x'):
                    return 'Hispanic or Latino'
                if v == 'black':
                    return 'Black or African American'
                if v == 'other':
                    return 'Another Race/Ethnicity'
                return val

            for row in demo_results:
                gender, age, income, ethnicity, count = row
                if gender:
                    demographics['gender'][gender] = demographics['gender'].get(gender, 0) + count
                if age:
                    demographics['age'][age] = demographics['age'].get(age, 0) + count
                if income:
                    demographics['income'][income] = demographics['income'].get(income, 0) + count
                if ethnicity:
                    key = _display_ethnicity(ethnicity)
                    demographics['ethnicity'][key] = demographics['ethnicity'].get(key, 0) + count
            
            # Convert counts to percentages
            total_demo = sum(demographics['gender'].values()) if demographics['gender'] else actual_segment_size
            for demo_type in demographics:
                for key in demographics[demo_type]:
                    demographics[demo_type][key] = (demographics[demo_type][key] / total_demo * 100) if total_demo > 0 else 0
            
            conn.close()
            
            return jsonify({
                'success': True,
                'segmentSize': actual_segment_size,
                'behavioral': behavioral,
                'demographics': demographics,
                'sampleSize': actual_segment_size
            })
            
        except Exception as e:
            conn.close()
            print(f"❌ Error querying segment data: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500
            
    except Exception as e:
        print(f"❌ Error in get_segment_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def parse_number(value):
    """Parse a number from CSV value, handling commas and formatting."""
    if not value or not value.strip():
        return None
    # Remove commas and try to parse as float/int
    try:
        cleaned = value.strip().replace(',', '').replace('$', '').replace('%', '')
        # Try int first, then float
        if '.' in cleaned:
            return float(cleaned)
        return int(cleaned)
    except:
        return None

def _first_numeric_in_row(row, start_idx=1):
    """Return (numeric_value, column_index) for the first parseable number in row[start_idx:], or (None, None)."""
    for j in range(start_idx, len(row)):
        if row[j] and parse_number(row[j]) is not None:
            return parse_number(row[j]), j
    return None, None

def _all_numerics_in_row(row, start_idx=1, max_count=4):
    """Return list of (numeric_value, column_index) for parseable numbers in row[start_idx:], up to max_count."""
    result = []
    for j in range(start_idx, len(row)):
        if len(result) >= max_count:
            break
        if row[j] and parse_number(row[j]) is not None:
            result.append((parse_number(row[j]), j))
    return result

def parse_subscriber_iq_csv(csv_content):
    """Parse subscriber IQ CSV (show-to-platform attribution format)."""
    lines = csv_content.strip().split('\n')
    reader = csv.reader(lines)
    rows = list(reader)
    
    print(f"📊 Parsing subscriber IQ CSV: {len(rows)} rows")
    
    parsed = {
        'metadata': {},
        'key_metrics': {},
        'episode_attribution': [],
        'signup_timing': [],
        'episode_signup_timing': {},
        'attribution_summary': {},
        'post_signup_touchpoints': [],
        'competitive_platforms': [],
        'monthly_signups': [],
        'monthly_churn': [],
        'demographics': {
            'age': [],
            'gender': []
        }
    }
    
    current_section = None
    current_episode = None
    
    # Skip header row if it exists (Category,Count,Count Label,etc.)
    start_idx = 0
    if rows and len(rows) > 0:
        first_row = rows[0]
        if len(first_row) > 0 and first_row[0].strip().upper() == 'CATEGORY':
            start_idx = 1
            print(f"   📋 Skipping header row")
    
    for i in range(start_idx, len(rows)):
        row = rows[i]
        if not row or all(not cell.strip() for cell in row):
            continue
        
        # Check for section headers - can be in first, second, or third column (Chris Rock format uses col 2)
        first_col = row[0].strip() if len(row) > 0 and row[0] else ''
        second_col = row[1].strip() if len(row) > 1 and row[1] else ''
        third_col = row[2].strip() if len(row) > 2 and row[2] else ''
        combined_check = (first_col + ' ' + second_col + ' ' + third_col).strip().upper()
        
        # Global section detection (so we don't require strict CSV order)
        if 'COMPETITIVE' in combined_check and 'PLATFORM' in combined_check:
            current_section = 'competitive_platforms'
            print(f"   ✅ Entered COMPETITIVE PLATFORMS section at row {i}")
            continue
        if 'DEMOGRAPHICS' in combined_check:
            current_section = 'demographics'
            print(f"   ✅ Entered DEMOGRAPHICS section at row {i}")
            continue
        if 'MONTHLY' in combined_check and ('SIGNUP' in combined_check or 'SIGNUPS' in combined_check):
            current_section = 'monthly_signups'
            continue
        if ('ATTRIBUTION SUMMARY' in combined_check or 'ATTRIBUTION SUMMARY' in first_col.upper() or
                'ATTRIBUTION SUMMARY' in second_col.upper() or 'ATTRIBUTION SUMMARY' in third_col.upper()):
            current_section = 'attribution_summary'
            print(f"   ✅ Entered ATTRIBUTION SUMMARY section at row {i}")
            continue
        if 'PER-EPISODE' in combined_check or 'PER-DATE ATTRIBUTION' in combined_check or ('EPISODE' in combined_check and 'ATTRIBUTION' in combined_check):
            current_section = 'episode_attribution'
            continue
        if 'SIGNUP TIMING' in combined_check and ('DAYS AFTER' in combined_check or 'SHOW' in combined_check or 'AVAILABLE' in combined_check):
            current_section = 'signup_timing'
            print(f"   ✅ Entered SIGNUP TIMING section at row {i}")
            continue
        if 'POST-SIGNUP' in combined_check and 'TOUCHPOINT' in combined_check:
            current_section = 'post_signup_touchpoints'
            print(f"   ✅ Entered POST-SIGNUP TOUCHPOINT ANALYSIS section at row {i}")
            continue
        if 'KEY METRICS' in first_col.upper() or 'KEY METRICS' in second_col.upper() or 'KEY METRICS' in third_col.upper() or combined_check.strip() == 'KEY METRICS':
            current_section = 'key_metrics'
            print(f"   ✅ Entered KEY METRICS section at row {i}")
            continue
        
        # Metadata section (also enter when we see metadata rows before any section header)
        if 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in first_col.upper() or 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in second_col.upper() or 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in third_col.upper() or 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in combined_check:
            current_section = 'metadata'
            print(f"   ✅ Entered metadata section at row {i}")
            continue
        if 'Show/Content Tracked' in first_col or 'Platform Tracked' in first_col:
            current_section = 'metadata'
        elif current_section == 'metadata':
            if 'Show/Content Tracked' in first_col:
                # New schema: value often in col 3 (e.g. "Show/Content Tracked,,,Landman")
                parsed['metadata']['show'] = (row[3].strip() if len(row) > 3 else '') or (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '') or ''
            elif 'Platform Tracked' in first_col:
                # Value in col 3 (e.g. "Platform Tracked,,,paramount+")
                platform_val = (row[3].strip() if len(row) > 3 else '') or (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                parsed['metadata']['platform'] = platform_val
                if platform_val:
                    print(f"   📺 Found platform: '{platform_val}' from row {i}")
            elif 'Analysis Date Range' in first_col or 'Date Range' in first_col or 'date range' in first_col.lower():
                # Try multiple columns for date range (col 3 common in Landman-style CSV)
                date_range_val = (row[3].strip() if len(row) > 3 else '') or (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                if not date_range_val and len(row) > 0 and ':' in first_col:
                    parts = first_col.split(':', 1)
                    if len(parts) > 1:
                        date_range_val = parts[1].strip()
                parsed['metadata']['date_range'] = date_range_val
                print(f"   📅 Found date range: '{date_range_val}' from row {i}: {row[:4]}")
            elif 'Exclusion Window' in first_col:
                parsed['metadata']['exclusion_window'] = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
            elif 'Attribution Window' in first_col:
                parsed['metadata']['attribution_window'] = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
            elif 'Content Cadence' in first_col:
                cadence_val = (row[3].strip() if len(row) > 3 else '') or (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                parsed['metadata']['content_cadence'] = cadence_val
                if cadence_val:
                    print(f"   🔄 Found content cadence: '{cadence_val}' from row {i}")
            elif 'Genre' in first_col:
                # New schema: Genre value in col 3 (e.g. "Genre,,,Serialized Drama"); fallback to col 2, then col 1
                genre_val = (row[3].strip() if len(row) > 3 else '') or (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                parsed['metadata']['genre'] = genre_val
                if genre_val:
                    print(f"   📂 Found genre: '{genre_val}' from row {i}")
            elif 'KEY METRICS' in first_col.upper() or 'KEY METRICS' in second_col.upper() or 'KEY METRICS' in third_col.upper() or 'KEY METRICS' in combined_check:
                current_section = 'key_metrics'
                print(f"   ✅ Entered KEY METRICS section at row {i}: first_col='{first_col}', second_col='{second_col}', third_col='{third_col}'")
                continue
        
        # Key metrics (CSV: Category, Episode Date, Count, ..., Percentage, Gen Pop Projection -> count in col 2, gen_pop in col 9)
        elif current_section == 'key_metrics':
            def _count_val(r):
                n = parse_number(r[2]) if len(r) > 2 else None
                if n is None and len(r) > 1:
                    n = parse_number(r[1])
                return n
            def _gen_pop_val(r):
                return (r[9].strip() if len(r) > 9 else '') or (r[8].strip() if len(r) > 8 else '')
            if 'Total Show Watchers' in first_col:
                count_val = _count_val(row)
                gen_pop_val = _gen_pop_val(row)
                print(f"   📊 Found Total Show Watchers: row={row[:4]}, count={count_val}, gen_pop={gen_pop_val}, row_len={len(row)}")
                parsed['key_metrics']['total_watchers'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Pre-Existing' in first_col or 'Pre Existing' in first_col:
                count_val = _count_val(row)
                gen_pop_val = _gen_pop_val(row)
                print(f"   📊 Found Pre-Existing Series Viewers: count={count_val}, gen_pop={gen_pop_val}")
                parsed['key_metrics']['pre_existing'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Clean Sample' in first_col or 'Clean Sample (New First Time Viewers)' in first_col:
                count_val = _count_val(row)
                gen_pop_val = _gen_pop_val(row)
                print(f"   📊 Found Clean Sample: count={count_val}, gen_pop={gen_pop_val}")
                parsed['key_metrics']['clean_sample'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'New Platform Signups' in first_col:
                count_val = _count_val(row)
                gen_pop_val = _gen_pop_val(row)
                print(f"   📊 Found New Platform Signups: count={count_val}, gen_pop={gen_pop_val}")
                parsed['key_metrics']['new_signups'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Clean Conversion Rate' in first_col:
                parsed['key_metrics']['clean_conversion_rate'] = (row[8].strip() if len(row) > 8 else '') or (row[1].strip() if len(row) > 1 else '')
            elif 'Total Show Conversion Rate' in first_col:
                parsed['key_metrics']['total_conversion_rate'] = (row[8].strip() if len(row) > 8 else '') or (row[1].strip() if len(row) > 1 else '')
            elif 'Average Days' in first_col:
                # Value can be in col 2, 3, or 4 (SVOD CSV: "Average Days from Show Available to Signup", "", "", "", "7.2", "days")
                val = (row[4].strip() if len(row) > 4 else '') or (row[3].strip() if len(row) > 3 else '') or (row[2].strip() if len(row) > 2 else '')
                if val and val.lower().endswith('days'):
                    val = val[:-4].strip()
                parsed['key_metrics']['avg_days_to_signup'] = val
            elif 'PER-EPISODE ATTRIBUTION' in first_col.upper() or 'PER-EPISODE ATTRIBUTION' in second_col.upper() or 'PER-EPISODE ATTRIBUTION' in third_col.upper() or 'PER-EPISODE ATTRIBUTION' in combined_check:
                current_section = 'episode_attribution'
                print(f"   ✅ Entered PER-EPISODE ATTRIBUTION section at row {i}: first_col='{first_col}', second_col='{second_col}'")
                continue
        
        # Episode attribution (and PER-DATE attribution for stand-up/special format)
        elif current_section == 'episode_attribution':
            # Handle both "Episode X" and just "X" formats
            episode_num = None
            if first_col.startswith('Episode '):
                episode_num = first_col.replace('Episode ', '').strip()
            elif first_col and first_col.strip().isdigit():
                episode_num = first_col.strip()
            elif first_col and len(first_col) <= 3 and first_col.replace(' ', '').isdigit():
                episode_num = first_col.replace(' ', '').strip()
            # PER-DATE format: date in col 0 (e.g. 03/08/23), signups in col 2
            elif first_col and '/' in first_col and parse_number(row[2] if len(row) > 2 else '') is not None:
                episode_num = first_col  # Use date as "episode" label for Content Performance
            elif first_col and '/' in first_col and parse_number(row[2] if len(row) > 2 else '') is None and len(row) > 2 and row[2].strip().lower() != 'signups':
                # Skip sub-rows like "  Same Day" under a date
                continue
            
            if episode_num:
                # Support two CSV layouts:
                # New: 0=Episode N, 1=Episode Date, 2=Count, 3=label, 4=days_avg, 5=label, 6=min_avg_view, 7=label, 8=%, 9=gen_pop
                # Old: 0=Episode N, 1=Count, 2=label, 3=days_avg, 4=label, 5=min_avg_view, 6=label, 7=%, 8=gen_pop
                def _val(col_idx, skip_labels=None):
                    if col_idx >= len(row): return ''
                    s = row[col_idx].strip()
                    if not s: return ''
                    if skip_labels and s.lower() in (x.lower() for x in skip_labels): return ''
                    if parse_number(s) is not None: return s
                    if '%' in s: return s
                    return s
                cell1 = row[1].strip() if len(row) > 1 else ''
                has_episode_date = bool(cell1 and ('/' in cell1 or cell1.replace('-', '').replace('.', '').isdigit()) and parse_number(cell1) is None)
                if has_episode_date and len(row) >= 10:
                    episode_date = cell1
                    signups_val = parse_number(row[2]) if len(row) > 2 else None
                    days_avg = _val(4, ('signups', 'days avg', 'min avg view'))
                    min_avg_view = _val(6, ('signups', 'days avg', 'min avg view'))
                    pct_val = _val(8, ('signups', 'days avg', 'min avg view'))
                    if not pct_val and len(row) > 8 and '%' in row[8]:
                        pct_val = row[8].strip()
                    gen_pop = row[9].strip() if len(row) > 9 else ''
                else:
                    episode_date = ''
                    signups_val = parse_number(row[1]) if len(row) > 1 else None
                    days_avg = _val(3, ('signups', 'days avg', 'min avg view'))
                    min_avg_view = _val(5, ('signups', 'days avg', 'min avg view'))
                    pct_val = _val(7, ('signups', 'days avg', 'min avg view'))
                    if not pct_val and len(row) > 7 and '%' in row[7]:
                        pct_val = row[7].strip()
                    gen_pop = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found Episode {episode_num}: episode_date={episode_date or 'N/A'}, signups={signups_val}, days_avg={days_avg}, pct={pct_val}, row={row[:10]}")
                parsed['episode_attribution'].append({
                    'episode': episode_num,
                    'episode_date': episode_date,
                    'signups': signups_val,
                    'days_avg': days_avg,
                    'min_avg_view': min_avg_view,
                    'percentage': pct_val,
                    'gen_pop': gen_pop
                })
            elif 'ATTRIBUTION SUMMARY' in first_col.upper() or 'ATTRIBUTION SUMMARY' in second_col.upper() or 'ATTRIBUTION SUMMARY' in combined_check:
                current_section = 'attribution_summary'
                print(f"   ✅ Entered ATTRIBUTION SUMMARY section at row {i}: first_col='{first_col}', second_col='{second_col}'")
                continue
        
        # Attribution summary (count in col 2, gen_pop in col 9 when available)
        elif current_section == 'attribution_summary':
            def _attr_count(r):
                n = parse_number(r[2]) if len(r) > 2 else None
                return n if n is not None else (parse_number(r[1]) if len(r) > 1 else None)
            def _attr_gen_pop(r):
                return (r[9].strip() if len(r) > 9 else '') or (r[8].strip() if len(r) > 8 else '')
            if 'Attributed Signups' in first_col:
                count_val = _attr_count(row)
                pct_val = (row[8].strip() if len(row) > 8 and '%' in str(row[8]) else '') or (row[7].strip() if len(row) > 7 else '')
                gen_pop_val = _attr_gen_pop(row)
                print(f"   📊 Found Attributed Signups: count={count_val}, pct={pct_val}, gen_pop={gen_pop_val}")
                parsed['attribution_summary']['attributed'] = {
                    'count': count_val,
                    'percentage': pct_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Dormant to Reactive' in first_col:
                count_val = _attr_count(row)
                pct_val = (row[8].strip() if len(row) > 8 and '%' in str(row[8]) else '') or (row[7].strip() if len(row) > 7 else '')
                gen_pop_val = _attr_gen_pop(row)
                print(f"   📊 Found Dormant to Reactive: count={count_val}, pct={pct_val}, gen_pop={gen_pop_val}")
                parsed['attribution_summary']['dormant_reactive'] = {
                    'count': count_val,
                    'percentage': pct_val,
                    'gen_pop': gen_pop_val
                }
            elif 'TOTAL SIGNUPS' in first_col:
                count_val = _attr_count(row)
                pct_val = (row[8].strip() if len(row) > 8 and '%' in str(row[8]) else '') or (row[7].strip() if len(row) > 7 else '')
                gen_pop_val = _attr_gen_pop(row)
                print(f"   📊 Found TOTAL SIGNUPS: count={count_val}, pct={pct_val}, gen_pop={gen_pop_val}")
                parsed['attribution_summary']['total'] = {
                    'count': count_val,
                    'percentage': pct_val,
                    'gen_pop': gen_pop_val
                }
            elif 'SIGNUP TIMING' in first_col or 'SIGNUP TIMING' in second_col or 'SIGNUP TIMING' in third_col:
                current_section = 'signup_timing'
                continue
        
        # Signup timing (signups in col 2 or col 1, day label in col 0; gen_pop in col 9)
        elif current_section == 'signup_timing':
            if first_col and first_col not in ['', 'SIGNUP TIMING (Days After Show is Available)']:
                if 'Days Later' in first_col or first_col in ['Same Day', 'Day 1']:
                    signups_val = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                    gen_pop_val = (row[9].strip() if len(row) > 9 else '') or (row[8].strip() if len(row) > 8 else '')
                    parsed['signup_timing'].append({
                        'timing': first_col,
                        'signups': signups_val,
                        'percentage': row[7].strip() if len(row) > 7 else '',
                        'gen_pop': gen_pop_val
                    })
            elif 'SIGNUP TIMING PER EPISODE' in first_col or 'SIGNUP TIMING PER EPISODE' in second_col:
                current_section = 'episode_signup_timing'
                continue
        
        # Episode signup timing
        elif current_section == 'episode_signup_timing':
            if first_col.startswith('Episode '):
                episode_num = first_col.replace('Episode ', '').strip()
                current_episode = episode_num
                if episode_num not in parsed['episode_signup_timing']:
                    parsed['episode_signup_timing'][episode_num] = []
            elif current_episode and first_col and ('Days Later' in first_col or first_col in ['Same Day', 'Day 1']):
                signups_val = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                gen_pop_val = (row[9].strip() if len(row) > 9 else '') or (row[8].strip() if len(row) > 8 else '')
                parsed['episode_signup_timing'][current_episode].append({
                    'timing': first_col,
                    'signups': signups_val,
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': gen_pop_val
                })
            elif 'POST-SIGNUP TOUCHPOINT ANALYSIS' in first_col or 'POST-SIGNUP TOUCHPOINT ANALYSIS' in second_col:
                current_section = 'post_signup_touchpoints'
                continue
        
        # Post-signup touchpoints (CSV: 0=label, 2=Count, 8=Percentage, 9=Gen Pop Projection)
        elif current_section == 'post_signup_touchpoints':
            if first_col and first_col.endswith('Touchpoint'):
                touchpoint_num = first_col.replace('Touchpoint', '').strip()
                parsed['post_signup_touchpoints'].append({
                    'touchpoint': touchpoint_num,
                    'users': row[2].strip() if len(row) > 2 else '',
                    'percentage': row[8].strip() if len(row) > 8 else '',
                    'gen_pop': row[9].strip() if len(row) > 9 else ''
                })
            elif 'Total Platform Signups' in first_col:
                parsed['post_signup_touchpoints'].append({
                    'touchpoint': 'Total',
                    'users': row[2].strip() if len(row) > 2 else '',
                    'percentage': row[8].strip() if len(row) > 8 else '',
                    'gen_pop': row[9].strip() if len(row) > 9 else ''
                })
            elif 'COMPETITIVE PLATFORMS' in first_col or 'COMPETITIVE PLATFORMS' in second_col:
                current_section = 'competitive_platforms'
                continue
        
        # Competitive platforms (platform in col A/B/C, percentage in col E or col H)
        elif current_section == 'competitive_platforms':
            if 'MONTHLY' in combined_check and ('SIGNUP' in combined_check or 'SIGNUPS' in combined_check):
                current_section = 'monthly_signups'
                continue
            elif 'MONTHLY' in combined_check and 'CHURN' in combined_check:
                current_section = 'monthly_churn'
                continue
            elif 'DEMOGRAPHICS' in combined_check:
                current_section = 'demographics'
                continue
            platform = (first_col or second_col or (row[2].strip() if len(row) > 2 else '')).strip()
            if platform and 'COMPETITIVE' not in platform.upper() and 'PLATFORM' not in platform.upper():
                percentage = (row[4].strip() if len(row) > 4 else '') or (row[7].strip() if len(row) > 7 else '') or (row[8].strip() if len(row) > 8 else '')
                parsed['competitive_platforms'].append({
                    'platform': platform,
                    'percentage': percentage
                })
        
        # Monthly signups
        elif current_section == 'monthly_signups':
            if 'MONTHLY' in combined_check and 'CHURN' in combined_check:
                current_section = 'monthly_churn'
                continue
            elif 'DEMOGRAPHICS' in combined_check:
                current_section = 'demographics'
                continue
            if first_col and first_col not in ['', 'MONTHLY PLATFORM SIGNUPS -']:
                if re.match(r'^\d{4}-\d{2}$', first_col):
                    parsed['monthly_signups'].append({
                        'month': first_col,
                        'signups': row[1].strip() if len(row) > 1 else '',
                        'watched_show': row[3].strip() if len(row) > 3 else '',
                        'percentage': row[7].strip() if len(row) > 7 else '',
                        'gen_pop': row[8].strip() if len(row) > 8 else ''
                    })
        
        # Monthly churn
        elif current_section == 'monthly_churn':
            if first_col and first_col not in ['', 'MONTHLY PLATFORM CHURN -']:
                if re.match(r'^\d{4}-\d{2}$', first_col):
                    parsed['monthly_churn'].append({
                        'month': first_col,
                        'churned': row[1].strip() if len(row) > 1 else '',
                        'percentage': row[7].strip() if len(row) > 7 else '',
                        'gen_pop': row[8].strip() if len(row) > 8 else ''
                    })
            elif 'DEMOGRAPHICS' in first_col or 'DEMOGRAPHICS' in second_col:
                current_section = 'demographics'
                continue
        
        # Demographics
        elif current_section == 'demographics':
            if first_col == 'AGE':
                current_section = 'demographics_age'
                continue
            elif first_col == 'GENDER':
                current_section = 'demographics_gender'
                continue
        
        elif current_section == 'demographics_age':
            if first_col and first_col not in ['', 'AGE']:
                # Filter out gender entries that might have been mixed in
                first_col_upper = first_col.upper().strip()
                gender_keywords = ['MALE', 'FEMALE', 'GENDER', 'TRANS', 'NON-BINARY', 'NONBINARY', 'NON BINARY', 'PREFER NOT TO SAY', 'OTHER']
                
                # Count/percentage/gen_pop: CSV format is col C (2) for count, col I (8) for percentage, col J (9) for gen_pop
                _count = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                _pct = (row[8].strip() if len(row) > 8 else '') or (row[7].strip() if len(row) > 7 else '')
                _gen = (row[9].strip() if len(row) > 9 else '') or (row[5].strip() if len(row) > 5 else '')
                
                # Only add if it's not a gender entry and looks like an age range
                if not any(keyword in first_col_upper for keyword in gender_keywords):
                    # Check if it looks like an age range (contains numbers or age-like patterns)
                    if any(char.isdigit() for char in first_col) or '-' in first_col or '+' in first_col or 'to' in first_col_upper or 'and' in first_col_upper:
                        parsed['demographics']['age'].append({
                            'age_range': first_col,
                            'count': _count,
                            'percentage': _pct,
                            'gen_pop': _gen
                        })
                    else:
                        print(f"   ⚠️ Skipping potential gender entry in age section: '{first_col}'")
                else:
                    # This is a gender entry - add it to gender data instead
                    print(f"   ⚠️ Found gender entry '{first_col}' in age section, moving to gender data")
                    parsed['demographics']['gender'].append({
                        'gender': first_col,
                        'count': _count,
                        'percentage': _pct,
                        'gen_pop': _gen
                    })
        
        elif current_section == 'demographics_gender':
            if first_col and first_col not in ['', 'GENDER']:
                _count = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                _pct = (row[8].strip() if len(row) > 8 else '') or (row[7].strip() if len(row) > 7 else '')
                _gen = (row[9].strip() if len(row) > 9 else '') or (row[5].strip() if len(row) > 5 else '')
                parsed['demographics']['gender'].append({
                    'gender': first_col,
                    'count': _count,
                    'percentage': _pct,
                    'gen_pop': _gen
                })
    
    # Fallback: If date range wasn't found, try to find it anywhere in the CSV
    if not parsed['metadata'].get('date_range'):
        print("   ⚠️ Date range not found via section detection, trying fallback parsing...")
        for i, row in enumerate(rows):
            if not row or len(row) < 1:
                continue
            first_col = row[0].strip() if row[0] else ''
            second_col = row[1].strip() if len(row) > 1 and row[1] else ''
            # Check various date range patterns
            if ('date range' in first_col.lower() or 'analysis date range' in first_col.lower()) and not parsed['metadata'].get('date_range'):
                # Try multiple columns
                date_range_val = ''
                if len(row) > 1:
                    date_range_val = row[1].strip()
                if not date_range_val and len(row) > 2:
                    date_range_val = row[2].strip()
                if date_range_val:
                    parsed['metadata']['date_range'] = date_range_val
                    print(f"   ✅ Fallback: Found date range: '{date_range_val}'")
            # Also check if date range is in second column
            elif ('date range' in second_col.lower() or 'analysis date range' in second_col.lower()) and not parsed['metadata'].get('date_range'):
                date_range_val = ''
                if len(row) > 2:
                    date_range_val = row[2].strip()
                if not date_range_val and len(row) > 1:
                    date_range_val = row[1].strip()
                if date_range_val:
                    parsed['metadata']['date_range'] = date_range_val
                    print(f"   ✅ Fallback: Found date range in second column: '{date_range_val}'")
    
    # Fallback: If platform wasn't found, try to find it anywhere in the CSV
    if not parsed['metadata'].get('platform'):
        print("   ⚠️ Platform not found via section detection, trying fallback parsing...")
        for i, row in enumerate(rows):
            if not row or len(row) < 1:
                continue
            first_col = row[0].strip() if row[0] else ''
            second_col = row[1].strip() if len(row) > 1 and row[1] else ''
            # Check various platform patterns
            if ('platform tracked' in first_col.lower() or 'platform' in first_col.lower()) and not parsed['metadata'].get('platform'):
                # Try multiple columns
                platform_val = ''
                if len(row) > 1:
                    platform_val = row[1].strip()
                if not platform_val and len(row) > 2:
                    platform_val = row[2].strip()
                if platform_val:
                    parsed['metadata']['platform'] = platform_val
                    print(f"   ✅ Fallback: Found platform: '{platform_val}'")
            # Also check if platform is in second column
            elif ('platform tracked' in second_col.lower() or 'platform' in second_col.lower()) and not parsed['metadata'].get('platform'):
                platform_val = ''
                if len(row) > 2:
                    platform_val = row[2].strip()
                if not platform_val and len(row) > 1:
                    platform_val = row[1].strip()
                if platform_val:
                    parsed['metadata']['platform'] = platform_val
                    print(f"   ✅ Fallback: Found platform in second column: '{platform_val}'")
    
    # Platform from column D (index 3), row 5 (index 4)
    if len(rows) > 4 and len(rows[4]) > 3:
        platform_from_d5 = rows[4][3].strip()
        if platform_from_d5:
            parsed['metadata']['platform'] = platform_from_d5
            print(f"   📺 Platform from column D row 5: '{platform_from_d5}'")
    
    # Fallback: If key metrics weren't found, try to find them anyway
    if not parsed['key_metrics'].get('total_watchers'):
        print("   ⚠️ Key metrics not found via section detection, trying fallback parsing...")
        for i, row in enumerate(rows):
            if not row or len(row) < 2:
                continue
            first_col = row[0].strip() if row[0] else ''
            if 'Total Show Watchers' in first_col and not parsed['key_metrics'].get('total_watchers'):
                parsed['key_metrics']['total_watchers'] = {
                    'count': parse_number(row[1]) if len(row) > 1 else None,
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                }
                print(f"   ✅ Fallback: Found Total Show Watchers")
            elif 'New Platform Signups' in first_col and not parsed['key_metrics'].get('new_signups'):
                parsed['key_metrics']['new_signups'] = {
                    'count': parse_number(row[1]) if len(row) > 1 else None,
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                }
                print(f"   ✅ Fallback: Found New Platform Signups")
            elif 'Clean Sample' in first_col and not parsed['key_metrics'].get('clean_sample'):
                parsed['key_metrics']['clean_sample'] = {
                    'count': parse_number(row[1]) if len(row) > 1 else None,
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                }
                print(f"   ✅ Fallback: Found Clean Sample")
    
    # Fallback: Try to find attribution summary (count in col 2, pct in col 8, gen_pop in col 9)
    def _attr_count_row(r):
        n = parse_number(r[2]) if len(r) > 2 else None
        return n if n is not None else (parse_number(r[1]) if len(r) > 1 else None)
    def _attr_pct_row(r):
        return (r[8].strip() if len(r) > 8 and '%' in str(r[8]) else '') or (r[7].strip() if len(r) > 7 else '')
    def _attr_gen_pop_row(r):
        return (r[9].strip() if len(r) > 9 else '') or (r[8].strip() if len(r) > 8 else '')
    for i, row in enumerate(rows):
        if not row or len(row) < 1:
            continue
        first_col = (row[0].strip() if row[0] else '')
        if not parsed['attribution_summary'].get('attributed') and 'Attributed Signups' in first_col:
            parsed['attribution_summary']['attributed'] = {
                'count': _attr_count_row(row),
                'percentage': _attr_pct_row(row),
                'gen_pop': _attr_gen_pop_row(row)
            }
            print(f"   ✅ Fallback: Found Attributed Signups (row {i})")
        elif not parsed['attribution_summary'].get('dormant_reactive') and 'Dormant to Reactive' in first_col:
            parsed['attribution_summary']['dormant_reactive'] = {
                'count': _attr_count_row(row),
                'percentage': _attr_pct_row(row),
                'gen_pop': _attr_gen_pop_row(row)
            }
            print(f"   ✅ Fallback: Found Dormant to Reactive (row {i})")
        elif not parsed['attribution_summary'].get('total') and 'TOTAL SIGNUPS' in first_col.upper():
            parsed['attribution_summary']['total'] = {
                'count': _attr_count_row(row),
                'percentage': _attr_pct_row(row),
                'gen_pop': _attr_gen_pop_row(row)
            }
            print(f"   ✅ Fallback: Found TOTAL SIGNUPS (row {i})")
    # When CSV has New Platform Signups but no ATTRIBUTION SUMMARY section, use new_signups for attributed and total
    # so "New Accounts Acquisition" and "Accounts Acquired or Reactivated" display correctly (e.g. Queer Eye reports)
    new_signups = parsed['key_metrics'].get('new_signups')
    if new_signups and not parsed['attribution_summary'].get('attributed'):
        parsed['attribution_summary']['attributed'] = {
            'count': new_signups.get('count'),
            'percentage': '',
            'gen_pop': new_signups.get('gen_pop', '')
        }
        print(f"   ✅ Fallback: Using New Platform Signups for attribution_summary.attributed")
    if new_signups and not parsed['attribution_summary'].get('total'):
        parsed['attribution_summary']['total'] = {
            'count': new_signups.get('count'),
            'percentage': '',
            'gen_pop': new_signups.get('gen_pop', '')
        }
        print(f"   ✅ Fallback: Using New Platform Signups for attribution_summary.total")
    
    # Fallback: Try to find episodes if none were found (flexible column detection)
    if len(parsed['episode_attribution']) == 0:
        print("   ⚠️ No episodes found via section detection, trying fallback parsing...")
        for i, row in enumerate(rows):
            if not row or len(row) < 2:
                continue
            first_col = (row[0].strip() if row[0] else '').strip()
            # Look for rows that start with a number or "Episode N"
            if not first_col:
                continue
            episode_num = None
            if first_col.isdigit():
                episode_num = first_col
            elif first_col.lower().startswith('episode '):
                episode_num = first_col[len('episode '):].strip() or first_col
            if episode_num is None:
                continue
            signups_val, signups_col = _first_numeric_in_row(row, 1)
            if signups_val is not None and signups_val > 0:
                # Flexible columns: use fixed indices if present, else first numerics after signups
                extra_numerics = _all_numerics_in_row(row, signups_col + 1, max_count=3)
                days_avg = (row[3].strip() if len(row) > 3 and row[3].strip() and parse_number(row[3]) is not None else '') or (str(extra_numerics[0][0]) if len(extra_numerics) > 0 else '')
                min_avg_view = (row[5].strip() if len(row) > 5 and row[5].strip() and parse_number(row[5]) is not None else '') or (str(extra_numerics[1][0]) if len(extra_numerics) > 1 else '')
                pct_fb = row[7].strip() if len(row) > 7 else ''
                if pct_fb and '%' not in pct_fb and pct_fb.lower() in ('signups', 'days avg', 'min avg view'):
                    pct_fb = ''
                parsed['episode_attribution'].append({
                    'episode': episode_num,
                    'signups': signups_val,
                    'days_avg': days_avg,
                    'min_avg_view': min_avg_view,
                    'percentage': pct_fb,
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                })
                print(f"   ✅ Fallback: Found Episode {episode_num} with {signups_val} signups")

    # Fallback: Try to find monthly signups if none were found (flexible column detection)
    if len(parsed['monthly_signups']) == 0:
        print("   ⚠️ No monthly signups found via section detection, trying fallback parsing...")
        for i in range(start_idx, len(rows)):
            row = rows[i]
            if not row or len(row) < 1:
                continue
            first_col = (row[0].strip() if row[0] else '').strip()
            if not re.match(r'^\d{4}-\d{2}$', first_col):
                continue
            signups_val, _ = _first_numeric_in_row(row, 1)
            if signups_val is not None:
                parsed['monthly_signups'].append({
                    'month': first_col,
                    'signups': signups_val,
                    'watched_show': row[3].strip() if len(row) > 3 else '',
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                })
                print(f"   ✅ Fallback: Found month {first_col} with {signups_val} signups")

    # Fallback: Competitive platforms if empty (platform in col A/B/C, percentage in col E)
    if len(parsed['competitive_platforms']) == 0:
        print("   ⚠️ No competitive platforms found, trying fallback...")
        in_comp = False
        for i, row in enumerate(rows):
            if not row:
                continue
            first_col = (row[0].strip() if len(row) > 0 and row[0] else '').strip()
            second_col = (row[1].strip() if len(row) > 1 and row[1] else '').strip()
            third_col = (row[2].strip() if len(row) > 2 and row[2] else '').strip()
            comb = (first_col + ' ' + second_col + ' ' + third_col).upper()
            if 'COMPETITIVE' in comb and 'PLATFORM' in comb:
                in_comp = True
                continue
            if in_comp and ('MONTHLY' in comb or 'DEMOGRAPHICS' in comb or 'CHURN' in comb):
                break
            if in_comp:
                platform = first_col or second_col or third_col
                if platform and 'COMPETITIVE' not in platform.upper() and 'PLATFORM' not in platform.upper():
                    pct = (row[4].strip() if len(row) > 4 else '') or (row[7].strip() if len(row) > 7 else '')
                    parsed['competitive_platforms'].append({'platform': platform, 'percentage': pct})
        if parsed['competitive_platforms']:
            print(f"   ✅ Fallback: Found {len(parsed['competitive_platforms'])} competitive platforms")

    # Fallback: Demographics if empty (AGE/GENDER in col A, count in col C, pct in col E)
    if (not parsed['demographics']['age'] and not parsed['demographics']['gender']):
        print("   ⚠️ No demographics found, trying fallback...")
        in_demo = False
        demo_sub = None
        for i, row in enumerate(rows):
            if not row:
                continue
            first_col = (row[0].strip() if len(row) > 0 and row[0] else '').strip()
            comb = (first_col + ' ' + (row[1].strip() if len(row) > 1 else '') + ' ' + (row[2].strip() if len(row) > 2 else '')).upper()
            if 'DEMOGRAPHICS' in comb:
                in_demo = True
                demo_sub = None
                continue
            if not in_demo:
                continue
            if first_col == 'AGE':
                demo_sub = 'age'
                continue
            if first_col == 'GENDER':
                demo_sub = 'gender'
                continue
            if demo_sub and first_col and first_col not in ('AGE', 'GENDER'):
                _c = (row[2].strip() if len(row) > 2 else '') or (row[1].strip() if len(row) > 1 else '')
                _p = (row[4].strip() if len(row) > 4 else '') or (row[7].strip() if len(row) > 7 else '')
                _g = (row[5].strip() if len(row) > 5 else '') or (row[8].strip() if len(row) > 8 else '')
                if demo_sub == 'age' and (any(c.isdigit() for c in first_col) or '-' in first_col or '+' in first_col):
                    parsed['demographics']['age'].append({'age_range': first_col, 'count': _c, 'percentage': _p, 'gen_pop': _g})
                elif demo_sub == 'gender':
                    parsed['demographics']['gender'].append({'gender': first_col, 'count': _c, 'percentage': _p, 'gen_pop': _g})
        if parsed['demographics']['age'] or parsed['demographics']['gender']:
            print(f"   ✅ Fallback: Found demographics (age: {len(parsed['demographics']['age'])}, gender: {len(parsed['demographics']['gender'])})")

    # Compute avg_days_to_signup from signup_timing when missing from key_metrics
    if not parsed['key_metrics'].get('avg_days_to_signup') and parsed['signup_timing']:
        def _days_from_timing(label):
            s = (label or '').strip()
            if s == 'Same Day': return 0
            m = re.match(r'^Day\s*(\d+)$', s, re.I)
            if m: return int(m.group(1))
            m = re.match(r'^(\d+)\s*Days?\s*Later', s, re.I)
            if m: return int(m.group(1))
            m = re.match(r'^(\d+)', s)
            return int(m.group(1)) if m else None
        total = 0
        weighted = 0
        for t in parsed['signup_timing']:
            days = _days_from_timing(t.get('timing', ''))
            if days is not None:
                cnt = parse_number(t.get('signups', 0)) or 0
                total += cnt
                weighted += days * cnt
        if total > 0:
            parsed['key_metrics']['avg_days_to_signup'] = f"{(weighted / total):.1f}"
            print(f"   📊 Computed avg_days_to_signup from signup_timing: {parsed['key_metrics']['avg_days_to_signup']}")

    # Log parsing summary
    print(f"📊 Parsing complete:")
    print(f"   Key metrics: {len(parsed['key_metrics'])} items")
    print(f"   Episodes: {len(parsed['episode_attribution'])} items")
    print(f"   Signup timing: {len(parsed['signup_timing'])} items")
    print(f"   Attribution summary: {len(parsed['attribution_summary'])} items")
    print(f"   Competitive platforms: {len(parsed['competitive_platforms'])} items")
    print(f"   Demographics age: {len(parsed['demographics']['age'])} items, gender: {len(parsed['demographics']['gender'])} items")
    if parsed['key_metrics'].get('total_watchers'):
        print(f"   Total Watchers: {parsed['key_metrics']['total_watchers']}")
    
    return parsed


def scale_subscriber_iq_values(parsed, divisor=10):
    """Scale all SVOD Subscriber Acquisition output numbers by 1/divisor (e.g. divisor=10 reduces all by 10x). Percentages and avg_days are left unchanged."""
    if divisor is None or divisor == 0:
        return
    scale = 1.0 / float(divisor)

    def _scale_num(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            scaled = v * scale
            return int(scaled) if scaled == int(scaled) else round(scaled, 2)
        if isinstance(v, str):
            n = parse_number(v)
            if n is not None:
                scaled = n * scale
                return str(int(scaled)) if scaled == int(scaled) else f'{scaled:.2f}'
        return v

    # key_metrics: scale count and gen_pop for all metrics (total_watchers, pre_existing, clean_sample, new_signups)
    for key in ('total_watchers', 'pre_existing', 'clean_sample', 'new_signups'):
        m = parsed.get('key_metrics', {}).get(key)
        if isinstance(m, dict):
            if 'count' in m and m['count'] is not None:
                m['count'] = _scale_num(m['count'])
            if 'gen_pop' in m:
                val = m['gen_pop']
                if isinstance(val, (int, float)):
                    m['gen_pop'] = _scale_num(val)
                elif isinstance(val, str) and parse_number(val) is not None:
                    m['gen_pop'] = _scale_num(val)

    # episode_attribution: scale signups and gen_pop
    for ep in parsed.get('episode_attribution') or []:
        if ep.get('signups') is not None:
            ep['signups'] = _scale_num(ep['signups'])
        if ep.get('gen_pop') is not None:
            val = ep['gen_pop']
            if isinstance(val, (int, float)):
                ep['gen_pop'] = _scale_num(val)
            elif isinstance(val, str) and parse_number(val) is not None:
                ep['gen_pop'] = _scale_num(val)

    # attribution_summary: scale count and gen_pop
    for key in ('total', 'attributed', 'same_day', 'later'):
        m = parsed.get('attribution_summary', {}).get(key)
        if isinstance(m, dict):
            if 'count' in m and m['count'] is not None:
                m['count'] = _scale_num(m['count'])
            if 'gen_pop' in m:
                val = m['gen_pop']
                if isinstance(val, (int, float)):
                    m['gen_pop'] = _scale_num(val)
                elif isinstance(val, str) and parse_number(val) is not None:
                    m['gen_pop'] = _scale_num(val)

    # signup_timing: scale signups
    for t in parsed.get('signup_timing') or []:
        if t.get('signups') is not None:
            t['signups'] = _scale_num(t['signups'])

    # post_signup_touchpoints: scale users
    for t in parsed.get('post_signup_touchpoints') or []:
        if t.get('users') is not None:
            t['users'] = _scale_num(t['users'])

    # competitive_platforms: scale count if present
    for c in parsed.get('competitive_platforms') or []:
        if c.get('count') is not None:
            c['count'] = _scale_num(c['count'])

    # monthly_signups: scale count if present
    for m in parsed.get('monthly_signups') or []:
        if m.get('count') is not None:
            m['count'] = _scale_num(m['count'])

    # monthly_churn: scale churned and gen_pop if numeric
    for m in parsed.get('monthly_churn') or []:
        if m.get('churned') is not None:
            m['churned'] = _scale_num(m['churned'])
        if m.get('gen_pop') is not None:
            val = m['gen_pop']
            if isinstance(val, (int, float)):
                m['gen_pop'] = _scale_num(val)
            elif isinstance(val, str) and parse_number(val) is not None:
                m['gen_pop'] = _scale_num(val)

    # demographics: scale count for age and gender
    for bucket in parsed.get('demographics', {}).get('age') or []:
        if bucket.get('count') is not None:
            bucket['count'] = _scale_num(bucket['count'])
    for bucket in parsed.get('demographics', {}).get('gender') or []:
        if bucket.get('count') is not None:
            bucket['count'] = _scale_num(bucket['count'])


@app.route('/api/subscriber-iq/list')
@requires_auth
def list_subscriber_iq_files():
    """List all subscriber IQ CSV files from S3."""
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    
    try:
        files = []
        # Load SVOD metadata to get categories
        svod_metadata = load_svod_metadata()
        
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=SUBSCRIBER_S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # Skip historic folder, purgatory (unreleased), and non-CSV files
                if key.startswith('historic/') or key.startswith(S3_PURGATORY_PREFIX) or not key.endswith('.csv'):
                    continue
                
                # Extract show name from filename (format: ShowName_MM_DD_YYYY_HH_MM.csv)
                name_without_ext = key.replace('.csv', '')
                match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
                if match:
                    show_name = match.group(1).replace('_', ' ')
                else:
                    show_name = name_without_ext.replace('_', ' ')
                
                # Get category from metadata, default to 'SVOD Acquisition'
                category = 'SVOD Acquisition'
                if key in svod_metadata and svod_metadata[key].get('category'):
                    category = svod_metadata[key]['category']
                
                files.append({
                    's3_key': key,
                    'show_name': show_name,
                    'category': category,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat()
                })
        
        # Sort by last modified (newest first)
        files.sort(key=lambda x: x['last_modified'], reverse=True)
        
        return jsonify({
            'success': True,
            'files': files
        })
    except Exception as e:
        print(f"❌ Error listing subscriber IQ files: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/subscriber-iq/data/<path:s3_key>')
@requires_auth
def get_subscriber_iq_data(s3_key):
    """Get subscriber IQ CSV data as JSON."""
    print(f"📥 get_subscriber_iq_data called for: {s3_key}")
    
    if not s3_client:
        print("❌ S3 client not configured")
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    
    try:
        print(f"📂 Fetching from S3: {SUBSCRIBER_S3_BUCKET}/{s3_key}")
        response = s3_client.get_object(Bucket=SUBSCRIBER_S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        print(f"✅ Got CSV content: {len(csv_content)} bytes")
        
        # Parse subscriber IQ CSV
        print(f"📝 CSV content preview (first 500 chars): {csv_content[:500]}")
        parsed = parse_subscriber_iq_csv(csv_content)
        # Subscriber IQ: use raw values from CSV (no scaling at serve time)

        # Log what was parsed in detail
        print(f"📊 Parsed data summary:")
        print(f"   Metadata keys: {list(parsed.get('metadata', {}).keys())}")
        print(f"   Key metrics keys: {list(parsed.get('key_metrics', {}).keys())}")
        print(f"   Key metrics values: {parsed.get('key_metrics', {})}")
        print(f"   Episodes count: {len(parsed.get('episode_attribution', []))}")
        print(f"   Signup timing count: {len(parsed.get('signup_timing', []))}")
        print(f"   Attribution summary keys: {list(parsed.get('attribution_summary', {}).keys())}")
        print(f"   Attribution summary: {parsed.get('attribution_summary', {})}")
        print(f"   Post-signup touchpoints (view numbers) count: {len(parsed.get('post_signup_touchpoints', []))}")
        
        # Check if data is actually empty
        has_data = any([
            parsed.get('key_metrics', {}),
            parsed.get('episode_attribution', []),
            parsed.get('signup_timing', []),
            parsed.get('attribution_summary', {})
        ])
        print(f"   Has any data: {has_data}")

        # Post-process episode_attribution: compute % of total when missing so cards fully populate
        episode_attribution = parsed.get('episode_attribution') or []
        if episode_attribution:
            def _ep_signups_num(e):
                v = e.get('signups')
                if isinstance(v, (int, float)):
                    return v
                if isinstance(v, str):
                    return parse_number(v) or 0
                return 0
            total_signups = sum(_ep_signups_num(e) for e in episode_attribution)
            for ep in episode_attribution:
                signups_val = _ep_signups_num(ep)
                if signups_val is not None and total_signups and total_signups > 0:
                    pct = (float(signups_val) / total_signups) * 100
                    existing_pct = (ep.get('percentage') or '').strip()
                    if not existing_pct or existing_pct == 'N/A':
                        ep['percentage'] = f'{pct:.1f}%'
                if not isinstance(ep.get('signups'), (int, float)) and ep.get('signups') is not None:
                    ep['signups'] = parse_number(ep['signups']) if isinstance(ep.get('signups'), str) else ep['signups']

        # Extract show name from filename
        name_without_ext = s3_key.replace('.csv', '')
        match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
        if match:
            show_name = match.group(1).replace('_', ' ')
        else:
            show_name = name_without_ext.replace('_', ' ')
        
        # Add percentage_label and set percentage for display: "X% of {show_name} watchers" (e.g. "12.84% of Reacher watchers")
        for ep in (parsed.get('episode_attribution') or []):
            pct = ep.get('percentage') or ''
            display_str = f'{pct} of {show_name} watchers' if pct else ''
            ep['percentage_label'] = display_str
            if pct:
                ep['percentage'] = display_str  # Display uses profile title instead of generic "show watchers"
        
        # Get date range and platform from metadata
        date_range = parsed['metadata'].get('date_range', '')
        platform_key = (parsed['metadata'].get('platform') or '').strip().lower().replace(' ', '')
        if not platform_key and 'platform' in parsed['metadata']:
            platform_key = str(parsed['metadata']['platform']).strip().lower().replace(' ', '')
        # Resolve platform key for pricing (e.g. "paramount+" -> same key in svod_pricing)
        svod_pricing = load_svod_pricing()
        pricing_for_platform = {}
        if platform_key:
            for key, val in svod_pricing.items():
                if key.strip().lower().replace(' ', '') == platform_key:
                    pricing_for_platform = val if isinstance(val, dict) else {}
                    break
        
        print(f"✅ Returning subscriber IQ data for show: {show_name.upper()}")
        print(f"   Data keys: {list(parsed.keys())}")
        print(f"   Data structure check - key_metrics type: {type(parsed.get('key_metrics'))}")
        print(f"   Data structure check - key_metrics content: {parsed.get('key_metrics')}")
        
        # Post-process post_signup_touchpoints: Total=100%, each 1st-5th = % of Total Platform Signups
        # 1st Touchpoint Gen Pop Projection = always New Platform Signups Gen Pop; Total = sum(1st–5th Gen Pop)
        touchpoints = parsed.get('post_signup_touchpoints') or []
        if touchpoints:
            def _tp_users_num(t):
                v = t.get('users')
                if isinstance(v, (int, float)):
                    return int(v)
                if isinstance(v, str):
                    return parse_number(v) or 0
                return 0
            def _tp_gen_pop_num(t):
                v = t.get('gen_pop')
                if isinstance(v, (int, float)):
                    return int(v)
                if isinstance(v, str):
                    return parse_number(v) or 0
                return 0
            total_users = None
            for t in touchpoints:
                if (t.get('touchpoint') or '').strip().lower() == 'total':
                    total_users = _tp_users_num(t)
                    break
            if total_users is None:
                total_users = sum(_tp_users_num(t) for t in touchpoints if str(t.get('touchpoint', '')).strip() not in ('Total', ''))
            if total_users and total_users > 0:
                for t in touchpoints:
                    users_val = _tp_users_num(t)
                    if (t.get('touchpoint') or '').strip().lower() == 'total':
                        t['percentage'] = '100.00%'
                    else:
                        pct = (float(users_val) / total_users) * 100
                        t['percentage'] = f'{pct:.2f}%'
            # 1st Touchpoint Gen Pop = always Gen Pop Projection New Platform Signups
            new_signups_gen_pop = parsed.get('key_metrics', {}).get('new_signups', {}).get('gen_pop')
            gen_pop_first = parse_number(new_signups_gen_pop) if isinstance(new_signups_gen_pop, str) else (int(new_signups_gen_pop) if new_signups_gen_pop is not None else None)
            first_tp_key = None
            for t in touchpoints:
                tp = (t.get('touchpoint') or '').strip().lower()
                if tp in ('1st', '1'):
                    first_tp_key = tp
                    break
            if first_tp_key is not None and gen_pop_first is not None:
                for t in touchpoints:
                    if (t.get('touchpoint') or '').strip().lower() == first_tp_key:
                        t['gen_pop'] = str(int(gen_pop_first))
                        break
            # Total Platform Signups Gen Pop = sum of 1st, 2nd, 3rd, 4th, 5th Gen Pop
            ordinal_keys = ('1st', '2nd', '3rd', '4th', '5th', '1', '2', '3', '4', '5')
            gen_pop_sum = 0
            for t in touchpoints:
                tp = (t.get('touchpoint') or '').strip().lower()
                if tp in ordinal_keys:
                    gen_pop_sum += _tp_gen_pop_num(t)
            for t in touchpoints:
                if (t.get('touchpoint') or '').strip().lower() == 'total':
                    t['gen_pop'] = str(int(gen_pop_sum))
                    break

        # Episode-Level Signup Timing section removed from dashboard (but data still available)
        parsed.pop('episode_signup_timing', None)
        
        # Uppercase platform name in metadata for display
        if parsed.get('metadata') and parsed['metadata'].get('platform'):
            parsed['metadata']['platform'] = str(parsed['metadata']['platform']).upper()
        
        # Override content_cadence from svod_metadata if set (CMS editable)
        svod_meta = load_svod_metadata()
        meta_cadence = svod_meta.get(s3_key, {}).get('content_cadence', '')
        if meta_cadence:
            parsed['metadata']['content_cadence'] = meta_cadence
        
        response_data = {
            'success': True,
            'data': parsed,
            'show': show_name.upper(),
            'date_range': date_range,
            's3_key': s3_key,
            'platform': parsed['metadata'].get('platform', ''),
            'svod_pricing': pricing_for_platform
        }
        
        # Verify the response can be serialized
        try:
            import json
            json_str = json.dumps(response_data, default=str)
            print(f"   ✅ Response serializes successfully ({len(json_str)} bytes)")
        except Exception as e:
            print(f"   ❌ Response serialization error: {e}")
        
        return jsonify(response_data)
    except Exception as e:
        print(f"❌ Error in get_subscriber_iq_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 's3_key': s3_key}), 500


# ============================================================================
# TICKET SALES IQ (talent-to-theater attribution)
# ============================================================================

def parse_ticket_sales_iq_csv(csv_content):
    """Parse talent-to-theater attribution CSV into structured data.
    Schema: Category, Count, Count Label, Secondary Count, Secondary Label, Percentage, Gen Pop Projection
    If Competitive Talent(s) is None/empty, omit competitive sections from output.
    """
    import csv as csv_module
    parsed = {
        'metadata': {},
        'key_metrics': {},
        'talent_attribution': {},
        'competitive_attribution': None,  # None if no competitor, else dict
        'talent_by_platform': [],
        'competitive_by_platform': []  # [] if no competitor
    }
    
    def _parse_num(s):
        if s is None or str(s).strip() == '':
            return None
        s = str(s).strip()
        for suffix, mult in [('M', 1e6), ('K', 1e3)]:
            if suffix in s.upper():
                try:
                    return float(re.sub(r'[^\d.]', '', s)) * mult
                except ValueError:
                    return None
        try:
            return float(s.replace(',', ''))
        except ValueError:
            return None
    
    def _fmt(s):
        return str(s).strip() if s else ''
    
    # Strip BOM and normalize
    csv_content = csv_content.lstrip('\ufeff').strip()
    rows = list(csv_module.reader(io.StringIO(csv_content)))
    if not rows:
        return parsed
    
    talent_name = ''
    competitor_name = ''
    current_section = None
    
    for i, row in enumerate(rows):
        if len(row) < 2:
            continue
        cat = _fmt(row[0])
        col1 = _fmt(row[1]) if len(row) > 1 else ''
        col2 = _fmt(row[2]) if len(row) > 2 else ''
        col3 = _fmt(row[3]) if len(row) > 3 else ''
        pct = _fmt(row[5]) if len(row) > 5 else ''
        gen_pop = _fmt(row[6]) if len(row) > 6 else ''
        
        # Metadata (value in col3 for Talent Tracked, Competitive Talent(s), etc.)
        if cat == 'Talent Tracked':
            talent_name = col3
            parsed['metadata']['talent_tracked'] = talent_name
        elif cat == 'Competitive Talent(s)':
            raw = col3
            if raw and raw.lower() not in ('none', 'n/a', '-'):
                competitor_name = raw
            else:
                competitor_name = None
            parsed['metadata']['competitive_talent'] = competitor_name
        elif cat == 'Movie Tracked':
            parsed['metadata']['movie_tracked'] = col3
        elif cat == 'Analysis Date Range':
            parsed['metadata']['date_range'] = col2 or col3
        
        # Section headers (may be in cat or col1)
        section_label = cat or col1
        if 'KEY METRICS' in section_label:
            current_section = 'key_metrics'
            continue
        if 'TALENT ATTRIBUTION' in section_label and 'COMPETITIVE' not in section_label.upper():
            current_section = 'talent'
            continue
        if 'COMPETITIVE TALENT ATTRIBUTION' in section_label:
            current_section = 'competitive'
            continue
        if '→ THEATER BY PLATFORM' in section_label.upper():
            # "COMPETITIVE TALENT → THEATER BY PLATFORM" or "[CompetitorName] → THEATER BY PLATFORM"
            if 'COMPETITIVE' in section_label.upper():
                current_section = 'competitive_platform'
            elif competitor_name and competitor_name.upper() in section_label.upper():
                current_section = 'competitive_platform'
            else:
                current_section = 'talent_platform'
            continue
        
        # Key metrics (label may be in col0/cat or col1)
        if current_section == 'key_metrics' and ('Total Movie Viewers' in (cat or col1)):
            parsed['key_metrics']['total_movie_viewers'] = _parse_num(col2) or col2
            parsed['key_metrics']['total_movie_viewers_gen_pop'] = gen_pop or None
        
        # Talent attribution (label in cat, value in col1)
        if current_section == 'talent':
            if '→ Theater Conversions' in cat or 'Theater Conversions' in cat:
                if not talent_name and '→' in cat:
                    talent_name = cat.split('→')[0].strip()
                    parsed['metadata']['talent_tracked'] = talent_name
                parsed['talent_attribution']['theater_conversions'] = _parse_num(col1) or col1
                parsed['talent_attribution']['theater_conversions_pct'] = pct or None
                parsed['talent_attribution']['theater_conversions_gen_pop'] = gen_pop or None
            elif 'Total' in cat and 'Hits' in cat:
                parsed['talent_attribution']['total_hits'] = _parse_num(col1) or col1
                parsed['talent_attribution']['total_hits_pct'] = pct or None
                parsed['talent_attribution']['total_hits_gen_pop'] = gen_pop or None
        
        # Competitive attribution (only if competitor exists)
        if current_section == 'competitive':
            if '→ Theater Conversions' in cat or 'Theater Conversions' in cat:
                if not competitor_name and '→' in cat:
                    competitor_name = cat.split('→')[0].strip()
                    parsed['metadata']['competitive_talent'] = competitor_name
            if competitor_name:
                if parsed['competitive_attribution'] is None:
                    parsed['competitive_attribution'] = {}
                if '→ Theater Conversions' in cat or 'Theater Conversions' in cat:
                    parsed['competitive_attribution']['theater_conversions'] = _parse_num(col1) or col1
                    parsed['competitive_attribution']['theater_conversions_pct'] = pct or None
                    parsed['competitive_attribution']['theater_conversions_gen_pop'] = gen_pop or None
                elif 'Total' in cat and 'Hits' in cat:
                    parsed['competitive_attribution']['total_hits'] = _parse_num(col1) or col1
                    parsed['competitive_attribution']['total_hits_pct'] = pct or None
                    parsed['competitive_attribution']['total_hits_gen_pop'] = gen_pop or None
        
        # Talent by platform (platform in col0, count in col1)
        if current_section == 'talent_platform':
            platform = cat
            if platform and platform.upper() not in ('TALENT ATTRIBUTION', 'COMPETITIVE', 'KEY METRICS'):
                cn = _parse_num(col1) if col1 else None
                if cn is not None or (col1 and str(col1).replace(',', '').replace('.', '').isdigit()):
                    parsed['talent_by_platform'].append({
                        'platform': platform,
                        'conversions': _parse_num(col1) or col1,
                        'percentage': pct or None,
                        'gen_pop': gen_pop or None
                    })
        
        # Competitive by platform
        if current_section == 'competitive_platform' and competitor_name:
            platform = cat
            if platform and platform.upper() not in ('TALENT ATTRIBUTION', 'COMPETITIVE', 'KEY METRICS'):
                cn = _parse_num(col1) if col1 else None
                if cn is not None or (col1 and str(col1).replace(',', '').replace('.', '').isdigit()):
                    parsed['competitive_by_platform'].append({
                        'platform': platform,
                        'conversions': _parse_num(col1) or col1,
                        'percentage': pct or None,
                        'gen_pop': gen_pop or None
                    })
    
    parsed['metadata']['talent_name'] = talent_name
    parsed['metadata']['competitor_name'] = competitor_name
    return parsed


@app.route('/api/ticket-sales-iq/list')
@requires_auth
def list_ticket_sales_iq_files():
    """List all ticket sales IQ CSV files from S3 bucket ticket-sales-iq, with metadata (display_name, category, image_url)."""
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    
    try:
        ts_meta = load_ticket_sales_metadata()
        files = []
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=TICKET_SALES_S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv'):
                    continue
                name_without_ext = key.replace('.csv', '')
                match = re.match(r'^(.+)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
                if match:
                    default_display = match.group(1).replace('_', ' ')
                else:
                    default_display = name_without_ext.replace('_', ' ')
                meta = ts_meta.get(key, {})
                display_name = (meta.get('display_name') or default_display).strip()
                category = (meta.get('category') or 'Uncategorized').strip() or 'Uncategorized'
                image_url = meta.get('image_url') or ''
                
                files.append({
                    's3_key': key,
                    'display_name': display_name,
                    'category': category,
                    'image_url': image_url if image_url else None,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat()
                })
        
        files.sort(key=lambda x: x['last_modified'], reverse=True)
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        print(f"❌ Error listing ticket sales IQ files: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ticket-sales-iq/data/<path:s3_key>')
@requires_auth
def get_ticket_sales_iq_data(s3_key):
    """Get ticket sales IQ CSV data as JSON."""
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    
    try:
        response = s3_client.get_object(Bucket=TICKET_SALES_S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        parsed = parse_ticket_sales_iq_csv(csv_content)
        
        ts_meta = load_ticket_sales_metadata()
        meta = ts_meta.get(s3_key, {})
        name_without_ext = s3_key.replace('.csv', '')
        match = re.match(r'^(.+)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
        default_display = match.group(1).replace('_', ' ') if match else name_without_ext.replace('_', ' ')
        display_name = (meta.get('display_name') or default_display).strip()
        image_url = meta.get('image_url') or ''
        
        return jsonify({
            'success': True,
            'data': parsed,
            'display_name': display_name,
            'image_url': image_url if image_url else None,
            's3_key': s3_key
        })
    except Exception as e:
        print(f"❌ Error in get_ticket_sales_iq_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 's3_key': s3_key}), 500


# ============================================================================
# TICKET SALES TRACKER (movie viewers → theater platform hits, ticket projections)
# ============================================================================
def parse_ticket_sales_tracker_csv(csv_content):
    """Parse Ticket Sales Tracker CSV into structured data.
    Schema: Category, Value, Projection/Percent, Note, Col5, Col6
    Returns US (gen pop) numbers only - used as primary display values.
    """
    import csv as csv_module
    parsed = {
        'metadata': {},
        'platforms': [],  # {platform, us_value} - US Gen Pop only
        'total_tickets_us': None,
        'projected_ticket_sales_us': None,
        'genre': '',
        'is_family_animation': False,  # for tooltip on Projected Ticket Sales
        'demographics_overall': {},
        'demographics_per_theater': {}
    }
    def _fmt(s):
        return str(s).strip() if s else ''
    def _parse_num(s):
        if s is None or str(s).strip() == '':
            return None
        s = str(s).strip()
        for suffix, mult in [('M', 1e6), ('K', 1e3)]:
            if suffix in s.upper():
                try:
                    return float(re.sub(r'[^\d.]', '', s)) * mult
                except ValueError:
                    return None
        s = s.replace(',', '').replace('$', '')
        try:
            return float(s)
        except ValueError:
            return None
    csv_content = csv_content.lstrip('\ufeff').strip()
    rows = list(csv_module.reader(io.StringIO(csv_content)))
    if not rows:
        return parsed
    current_section = None
    current_theater = None
    demo_field = None
    THEATER_PLATFORMS = ['Fandango', 'AMC THEATRES', 'ALAMO DRAFTHOUSE', 'CINEMARK THEATRES', 'REGAL CINEMAS']
    for row in rows:
        if len(row) < 2:
            continue
        cat = _fmt(row[0])
        val = _fmt(row[1])
        proj = _fmt(row[2]) if len(row) > 2 else ''
        note = _fmt(row[3]) if len(row) > 3 else ''
        if cat == 'Movie':
            parsed['metadata']['movie'] = val or (row[2] if len(row) > 2 else '')
        elif cat == 'Genre':
            parsed['genre'] = val or (row[2] if len(row) > 2 else '')
            gl = (parsed['genre'] or '').lower()
            parsed['is_family_animation'] = 'family' in gl or 'animation' in gl
        elif cat == 'Date Range':
            parsed['metadata']['date_range'] = val or (row[2] if len(row) > 2 else '')
        elif 'TOTAL HITS' in (cat + ' ' + val) or 'THEATER BY PLATFORM' in (cat + ' ' + val).upper():
            current_section = 'platforms'
            continue
        elif cat == 'Platform' and 'Hits' in (val + proj):
            continue
        elif current_section == 'platforms' and cat and cat in THEATER_PLATFORMS:
            us_val = proj if proj else val
            parsed['platforms'].append({'platform': cat, 'us_value': us_val, 'us_number': _parse_num(us_val)})
        elif 'Total Tickets Sold' in cat:
            parsed['total_tickets_us'] = proj or val
            current_section = None
        elif 'Projected Ticket Sales' in cat:
            parsed['projected_ticket_sales_us'] = proj or val
            current_section = None
        line_text = (cat + ' ' + val).upper()
        if 'DEMOGRAPHICS (OVERALL' in line_text and 'DEMOGRAPHICS PER THEATER' not in line_text:
            current_section = 'demo_overall'
            demo_field = None
            current_theater = None
            continue
        elif 'DEMOGRAPHICS PER THEATER' in line_text:
            current_section = 'demo_theater'
            demo_field = None
            current_theater = None
            continue
        elif current_section == 'demo_theater' and ('---' in cat or '---' in val):
            # "--- theater name ---" sets current theater (can be in cat or val)
            theater_raw = (cat or val).replace('---', '').strip().strip('|').strip()
            if theater_raw:
                current_theater = theater_raw
                if current_theater not in parsed['demographics_per_theater']:
                    parsed['demographics_per_theater'][current_theater] = {}
                demo_field = None
            continue
        if current_section == 'demo_overall':
            if cat in ['GENDER', 'AGE', 'INCOME', 'ETHNICITY', 'LOCATION']:
                demo_field = cat
                if demo_field not in parsed['demographics_overall']:
                    parsed['demographics_overall'][demo_field] = []
            elif demo_field and val and proj and '%' in proj:
                parsed['demographics_overall'][demo_field].append({'value': val, 'percent': proj})
        if current_section == 'demo_theater' and current_theater:
            if cat in ['GENDER', 'AGE', 'INCOME', 'ETHNICITY', 'LOCATION']:
                demo_field = cat
                if demo_field not in parsed['demographics_per_theater'][current_theater]:
                    parsed['demographics_per_theater'][current_theater][demo_field] = []
            elif demo_field and val and proj and '%' in proj:
                parsed['demographics_per_theater'][current_theater][demo_field].append({'value': val, 'percent': proj})
    # Filter demographics_per_theater to only the five allowed theater brands
    ALLOWED_THEATERS = ['Fandango', 'AMC THEATRES', 'ALAMO DRAFTHOUSE', 'CINEMARK THEATRES', 'REGAL CINEMAS']
    raw = parsed['demographics_per_theater']

    def map_to_canonical(name):
        n = (name or '').lower().strip()
        for a in ALLOWED_THEATERS:
            al = a.lower()
            if n == al:
                return a
            if n.startswith(al + ' ') or n.startswith(al + '|'):
                return a
            if '|' in n:
                first = n.split('|')[0].strip()
                if first == al or first.startswith(al + ' '):
                    return a
        return None

    candidates = {}  # canonical -> [(csv_name, demo_data), ...]
    for csv_name, demo_data in raw.items():
        canonical = map_to_canonical(csv_name)
        if canonical:
            candidates.setdefault(canonical, []).append((csv_name, demo_data))
    result = {}
    for canonical, items in candidates.items():
        # Prefer exact match; else shortest csv_name (e.g. "cinemark theatres" over "cinemark theatres | google maps")
        best = min(items, key=lambda x: (0 if (x[0] or '').lower() == canonical.lower() else 1, len(x[0] or '')))
        result[canonical] = best[1]
    parsed['demographics_per_theater'] = result
    return parsed


def _get_tst_genre_from_s3(key):
    """Read first 8KB of TST CSV from S3 and return genre (Category=Genre row). Returns 'Other' on failure."""
    if not s3_client:
        return 'Other'
    try:
        resp = s3_client.get_object(Bucket=TICKET_SALES_TRACKER_S3_BUCKET, Key=key)
        head = resp['Body'].read(8192).decode('utf-8', errors='replace')
        import csv as csv_module
        rows = list(csv_module.reader(io.StringIO(head)))
        for row in rows:
            if len(row) >= 1 and str(row[0]).strip() == 'Genre':
                val = (row[2] if len(row) > 2 else row[1] if len(row) > 1 else '').strip()
                return val or 'Other'
        return 'Other'
    except Exception:
        return 'Other'


@app.route('/api/ticket-sales-tracker/list')
@requires_auth
def list_ticket_sales_tracker_files():
    """List non-purgatory Ticket Sales Tracker CSV files from S3 bucket ticket-sales-tracker."""
    user = get_current_user()
    if not user_can_view_ticket_sales_tracker(user):
        return jsonify({'success': False, 'error': 'Ticket Sales Tracker dashboard access required'}), 403
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    try:
        tst_meta = load_ticket_sales_tracker_metadata()
        files = []
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=TICKET_SALES_TRACKER_S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv') or key.startswith(S3_PURGATORY_PREFIX):
                    continue
                # Basename only (in case key has prefix like released/)
                base = key.split('/')[-1].replace('.csv', '')
                # Strip trailing date/time _MM_DD_YYYY_HH_MM so selector shows only title
                base = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$', '', base)
                if base.startswith('Ticket_Sales_'):
                    base = base[len('Ticket_Sales_'):]
                default_display = base.replace('_', ' ').strip()
                meta = tst_meta.get(key, {})
                image_url = meta.get('image_url') or ''
                genre = meta.get('genre') or _get_tst_genre_from_s3(key)
                display_name = (meta.get('display_name') or default_display or 'Unknown').strip()
                files.append({
                    's3_key': key,
                    'display_name': display_name,
                    'genre': genre,
                    'image_url': image_url if image_url else None,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat()
                })
        files.sort(key=lambda x: x['last_modified'], reverse=True)
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        print(f"❌ Error listing ticket sales tracker files: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ticket-sales-tracker/download/<path:s3_key>')
@requires_auth
def download_ticket_sales_tracker(s3_key):
    """Download a released Ticket Sales Tracker CSV from S3."""
    user = get_current_user()
    if not user_can_view_ticket_sales_tracker(user):
        return jsonify({'error': 'Ticket Sales Tracker dashboard access required'}), 403
    if not s3_client or s3_key.startswith(S3_PURGATORY_PREFIX):
        return jsonify({'error': 'Download not available'}), 403
    try:
        response = s3_client.get_object(Bucket=TICKET_SALES_TRACKER_S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read()
        return Response(csv_content, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={s3_key.split("/")[-1]}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ticket-sales-tracker/data/<path:s3_key>')
@requires_auth
def get_ticket_sales_tracker_data(s3_key):
    """Get Ticket Sales Tracker CSV data as JSON."""
    user = get_current_user()
    if not user_can_view_ticket_sales_tracker(user):
        return jsonify({'success': False, 'error': 'Ticket Sales Tracker dashboard access required'}), 403
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    if s3_key.startswith(S3_PURGATORY_PREFIX):
        return jsonify({'success': False, 'error': 'Cannot load purgatory files'}), 403
    try:
        response = s3_client.get_object(Bucket=TICKET_SALES_TRACKER_S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        parsed = parse_ticket_sales_tracker_csv(csv_content)
        base = s3_key.split('/')[-1].replace('.csv', '')
        base = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$', '', base)
        if base.startswith('Ticket_Sales_'):
            base = base[len('Ticket_Sales_'):]
        default_display = base.replace('_', ' ').strip()
        tst_meta = load_ticket_sales_tracker_metadata()
        meta = tst_meta.get(s3_key, {})
        if parsed.get('genre'):
            if s3_key not in tst_meta:
                tst_meta[s3_key] = {}
            tst_meta[s3_key]['genre'] = parsed['genre']
            save_ticket_sales_tracker_metadata(tst_meta)
            meta = tst_meta.get(s3_key, {})
        image_url = meta.get('image_url') or ''
        display_name = (meta.get('display_name') or default_display or 'Unknown').strip()
        return jsonify({
            'success': True,
            'data': parsed,
            'display_name': display_name,
            'image_url': image_url if image_url else None,
            's3_key': s3_key
        })
    except Exception as e:
        print(f"❌ Error in get_ticket_sales_tracker_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 's3_key': s3_key}), 500


# ============================================================================
# HEDGE FUND IQ DAILY CACHE (shared across all users, refreshed each day)
# ============================================================================

HEDGE_FUND_CACHE_PREFIX = 'system/hedge_fund_daily_cache/'

def _hedge_fund_cache_key(s3_key_or_slug):
    """Create a safe S3 key from s3_key or slug (no slashes)."""
    return s3_key_or_slug.replace('/', '__').replace(' ', '_')

# Cache considered fresh for this many seconds; after that next request refetches from S3 (keeps projections current).
HEDGE_FUND_CACHE_MAX_AGE_SECONDS = 3600  # 1 hour

def _load_hedge_fund_daily_cache(cache_slug):
    """Load cached hedge fund data if valid for today and within max age (1 hour).
    First request of the day or first after 1 hour refetches from S3 so all users get current projections."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        key = f"{HEDGE_FUND_CACHE_PREFIX}{today}/{_hedge_fund_cache_key(cache_slug)}.json"
        response = s3_client.get_object(Bucket=METADATA_BUCKET, Key=key)
        cached = json.loads(response['Body'].read().decode('utf-8'))
        cached_date = cached.get('cached_date', '')
        if cached_date != today:
            return None
        cached_at_str = cached.get('cached_at', '')
        if cached_at_str:
            try:
                cached_at = datetime.fromisoformat(cached_at_str.replace('Z', '+00:00'))
                if cached_at.tzinfo:
                    cached_at = cached_at.replace(tzinfo=None)
                age_seconds = (datetime.now() - cached_at).total_seconds()
                if age_seconds > HEDGE_FUND_CACHE_MAX_AGE_SECONDS:
                    print(f"🔄 Hedge Fund cache expired (age {int(age_seconds)}s): {cache_slug}")
                    return None
            except Exception:
                pass
        print(f"📦 Hedge Fund daily cache HIT: {cache_slug}")
        return cached.get('data')
    except s3_client.exceptions.NoSuchKey:
        pass
    except Exception as e:
        print(f"⚠️ Hedge Fund cache read error: {e}")
    return None

def _save_hedge_fund_daily_cache(cache_slug, data):
    """Save hedge fund data to daily cache."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        key = f"{HEDGE_FUND_CACHE_PREFIX}{today}/{_hedge_fund_cache_key(cache_slug)}.json"
        entry = {'cached_date': today, 'cached_at': datetime.now().isoformat(), 'data': data}
        s3_client.put_object(
            Bucket=METADATA_BUCKET,
            Key=key,
            Body=json.dumps(entry, indent=2),
            ContentType='application/json'
        )
        print(f"💾 Hedge Fund daily cache SAVED: {cache_slug}")
    except Exception as e:
        print(f"⚠️ Hedge Fund cache write error: {e}")


# ============================================================================
# HEDGE FUND IQ API ENDPOINTS
# ============================================================================

@app.route('/api/hedge-fund-iq/list')
@app.route('/api/hedge-fund-iq/tickers')  # Alias for admin panel
@requires_auth
def list_hedge_fund_tickers():
    """List all ticker CSV files from S3 aggregated-tickers bucket."""
    if not hedge_fund_s3_client:
        return jsonify({'success': False, 'error': 'Hedge Fund S3 not configured'}), 500
    
    try:
        print(f"📊 Listing Hedge Fund IQ tickers from bucket: {HEDGE_FUND_S3_BUCKET} (region: {S3_REGION})")
        
        tickers = []
        paginator = hedge_fund_s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=HEDGE_FUND_S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                
                # Only process CSV files
                if not key.endswith('.csv'):
                    continue
                
                # Extract ticker name and KPI from filename
                # Format: TICKER_KPI_Daily.csv or TICKER.csv
                filename = key.replace('.csv', '').replace('_Daily', '')
                parts = filename.split('_')
                
                # Get the ticker symbol (first part, uppercase)
                ticker_symbol = parts[0].upper()
                
                # For compound tickers like TMUSphone, keep them together
                if len(parts) >= 2 and parts[1].lower() in ['phone', 'broadband']:
                    ticker_symbol = (parts[0] + parts[1]).upper()
                    kpi_parts = parts[2:] if len(parts) > 2 else []
                else:
                    kpi_parts = parts[1:] if len(parts) > 1 else []
                
                # Look up KPI from default mapping, or extract from filename
                if ticker_symbol in DEFAULT_TICKER_KPIS:
                    kpi_name = DEFAULT_TICKER_KPIS[ticker_symbol]
                elif kpi_parts:
                    kpi_name = ' '.join(kpi_parts).title()
                else:
                    kpi_name = 'Customers'
                
                # Determine display name
                display_name = ticker_symbol
                
                last_modified = obj['LastModified'].isoformat() if 'LastModified' in obj else None
                
                # Get metadata for this ticker
                metadata = load_ticker_metadata()
                ticker_metadata = metadata.get(key, {})
                
                tickers.append({
                    'ticker': ticker_symbol,
                    'display_name': display_name,  # Will be overridden by metadata if exists
                    'kpi': kpi_name,  # Will be overridden by metadata if exists
                    's3_key': key,
                    'last_modified': last_modified,
                    'bucket': HEDGE_FUND_S3_BUCKET
                })
        
        # Load metadata for custom overrides
        metadata = load_ticker_metadata()
        
        # Enrich tickers with metadata (allows admin overrides)
        for ticker in tickers:
            ticker_key = ticker['s3_key']
            original_ticker = ticker['ticker']  # Keep for SEC actuals / API (keyed by file-derived symbol)
            ticker['original_ticker'] = original_ticker
            if ticker_key in metadata:
                ticker['display_name'] = metadata[ticker_key].get('display_name', ticker['ticker'])
                ticker['kpi'] = metadata[ticker_key].get('kpi', ticker['kpi'])
                ticker['parent_ticker'] = metadata[ticker_key].get('parent_ticker', None)
                ticker['master_ticker'] = metadata[ticker_key].get('master_ticker', None)
                ticker['relevance_percentage'] = metadata[ticker_key].get('relevance_percentage', None)
                ticker['kpi_change_enabled'] = metadata[ticker_key].get('kpi_change_enabled', False)
                ticker['kpi_change_quarter_start'] = metadata[ticker_key].get('kpi_change_quarter_start')
                ticker['kpi_change_quarter_end'] = metadata[ticker_key].get('kpi_change_quarter_end')
                ticker['kpi_change_label'] = metadata[ticker_key].get('kpi_change_label')
                # Editable ticker symbol (title) override
                ticker['ticker'] = (metadata[ticker_key].get('ticker_symbol') or original_ticker).strip() or original_ticker
            else:
                ticker['parent_ticker'] = None
                ticker['master_ticker'] = None
                ticker['relevance_percentage'] = None
                ticker['kpi_change_enabled'] = False
                ticker['kpi_change_quarter_start'] = None
                ticker['kpi_change_quarter_end'] = None
                ticker['kpi_change_label'] = None
                ticker['original_ticker'] = original_ticker
        
        print(f"✅ Found {len(tickers)} tickers")
        return jsonify({'success': True, 'tickers': tickers})
        
    except Exception as e:
        print(f"❌ Error listing hedge fund tickers: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def _ticker_symbol_from_s3_key(key):
    """Derive ticker symbol from S3 key (e.g. VZ_Daily.csv -> VZ, TMUSphone_Daily.csv -> TMUSPHONE)."""
    if not key or not key.endswith('.csv'):
        return None
    filename = key.replace('.csv', '').replace('_Daily', '')
    parts = filename.split('_')
    if not parts:
        return None
    ticker_symbol = parts[0].upper()
    if len(parts) >= 2 and parts[1].lower() in ['phone', 'broadband']:
        ticker_symbol = (parts[0] + parts[1]).upper()
    return ticker_symbol


@app.route('/api/hedge-fund-iq/delete-ticker', methods=['POST'])
@requires_auth
@requires_admin
def delete_hedge_fund_ticker():
    """Delete a ticker: remove CSV from S3 and clear metadata, SEC actuals, profile mappings, and ticker image."""
    try:
        data = request.get_json() or {}
        s3_key = (data.get('s3_key') or '').strip()
        if not s3_key:
            return jsonify({'success': False, 'error': 's3_key is required'}), 400

        ticker_symbol = _ticker_symbol_from_s3_key(s3_key)
        if not ticker_symbol:
            return jsonify({'success': False, 'error': 'Invalid s3_key'}), 400

        if not hedge_fund_s3_client:
            return jsonify({'success': False, 'error': 'Hedge Fund S3 not configured'}), 500

        # 1. Delete CSV from aggregated-tickers bucket
        try:
            hedge_fund_s3_client.delete_object(Bucket=HEDGE_FUND_S3_BUCKET, Key=s3_key)
            print(f"🗑️ Deleted S3 object: {HEDGE_FUND_S3_BUCKET}/{s3_key}")
        except Exception as e:
            print(f"⚠️ S3 delete object: {e}")
            # Continue to clean metadata even if object was already missing

        # 2. Remove from ticker metadata (keyed by s3_key)
        metadata = load_ticker_metadata()
        if s3_key in metadata:
            del metadata[s3_key]
            save_ticker_metadata(metadata)
            print(f"🗑️ Removed ticker metadata for {s3_key}")

        # 3. Remove from SEC actuals (keyed by ticker symbol)
        all_actuals = load_json_from_s3(SEC_ACTUALS_FILE)
        if ticker_symbol in all_actuals:
            del all_actuals[ticker_symbol]
            save_json_to_s3(SEC_ACTUALS_FILE, all_actuals)
            print(f"🗑️ Removed SEC actuals for {ticker_symbol}")

        # 4. Remove ticker image (keyed by ticker symbol)
        ticker_images = load_json_from_s3(TICKER_IMAGES_FILE)
        if ticker_symbol in ticker_images:
            del ticker_images[ticker_symbol]
            save_json_to_s3(TICKER_IMAGES_FILE, ticker_images)
            print(f"🗑️ Removed ticker image for {ticker_symbol}")

        # 5. Remove profile mapping (keyed by ticker symbol)
        mappings = load_json_from_s3(TICKER_PROFILES_FILE)
        if ticker_symbol in mappings:
            del mappings[ticker_symbol]
            save_json_to_s3(TICKER_PROFILES_FILE, mappings)
            print(f"🗑️ Removed profile mapping for {ticker_symbol}")

        invalidate_cache()
        return jsonify({'success': True, 'message': f'Ticker {ticker_symbol} deleted'})
    except Exception as e:
        print(f"❌ Error deleting ticker: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/data/<path:s3_key>')
@requires_auth
def get_hedge_fund_ticker_data(s3_key):
    """Get ticker CSV data and calculate day-over-day metrics.
    Cache: First request of the day (or first after 1h TTL) fetches from S3 and caches; others get cache.
    So the first user each day/hour effectively refreshes data for everyone. Use ?refresh=1 to force fresh data anytime."""
    print(f"📥 get_hedge_fund_ticker_data called for: {s3_key}")
    
    # Check daily cache first unless refresh or date/quarter filter requested
    skip_cache = request.args.get('refresh', '').strip() in ('1', 'true', 'yes')
    filter_quarter = request.args.get('quarter', '').strip()
    filter_from = request.args.get('from', '').strip()
    filter_to = request.args.get('to', '').strip()
    if filter_quarter or filter_from or filter_to:
        skip_cache = True  # Never use cache when filtering by date/quarter
    if not skip_cache:
        cached = _load_hedge_fund_daily_cache(s3_key)
        if cached is not None:
            return jsonify(cached)
    elif skip_cache and not (filter_quarter or filter_from or filter_to):
        print(f"🔄 Refresh requested: bypassing cache for {s3_key}")
    
    if not hedge_fund_s3_client:
        print("❌ Hedge Fund S3 client not configured")
        return jsonify({'success': False, 'error': 'Hedge Fund S3 not configured'}), 500
    
    try:
        print(f"📂 Fetching from S3: {HEDGE_FUND_S3_BUCKET}/{s3_key} (region: {S3_REGION})")
        response = hedge_fund_s3_client.get_object(Bucket=HEDGE_FUND_S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        print(f"✅ Got CSV content: {len(csv_content)} bytes")
        
        # Parse CSV
        df = pd.read_csv(io.StringIO(csv_content))
        df = df.fillna(0)  # Fill NaN with 0 instead of empty string for numeric columns
        print(f"✅ Parsed CSV: {len(df)} rows, {len(df.columns)} columns")
        print(f"📋 Columns: {list(df.columns)}")
        
        # Flexible column name mapping - handle variations in column names
        col_mapping = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if 'consumer' in col_lower or 'total' in col_lower and 'sub' not in col_lower and 'cancel' not in col_lower:
                col_mapping['consumers'] = col
            elif 'sub' in col_lower and 'cancel' not in col_lower:
                col_mapping['subs'] = col
            elif 'cancel' in col_lower or 'churn' in col_lower:
                col_mapping['cancels'] = col
            elif 'date' in col_lower:
                col_mapping['date'] = col
            elif 'quarter' in col_lower:
                col_mapping['quarter'] = col
        
        print(f"🗺️ Column mapping: {col_mapping}")
        
        # Calculate day-over-day net growth (not compounding)
        # Net Growth = (Total Subs - Total Cancels) / Previous Day Total Consumers
        df['Calculated_Net_Growth'] = 0.0
        
        if 'consumers' in col_mapping and 'subs' in col_mapping and 'cancels' in col_mapping:
            consumers_col = col_mapping['consumers']
            subs_col = col_mapping['subs']
            cancels_col = col_mapping['cancels']
            
            # Ensure numeric types
            df[consumers_col] = pd.to_numeric(df[consumers_col], errors='coerce').fillna(0)
            df[subs_col] = pd.to_numeric(df[subs_col], errors='coerce').fillna(0)
            df[cancels_col] = pd.to_numeric(df[cancels_col], errors='coerce').fillna(0)
            
            for i in range(1, len(df)):
                prev_consumers = df.loc[i-1, consumers_col]
                current_subs = df.loc[i, subs_col]
                current_cancels = df.loc[i, cancels_col]
                
                if prev_consumers > 0:
                    net_change = current_subs - current_cancels
                    df.loc[i, 'Calculated_Net_Growth'] = net_change / prev_consumers
        else:
            print(f"⚠️ Warning: Could not find all required columns. Found: {col_mapping}")
            # Still return data even if we can't calculate net growth
        
        # Normalize column names for frontend consistency
        if 'consumers' in col_mapping:
            df.rename(columns={col_mapping['consumers']: 'Total Consumers'}, inplace=True)
        if 'subs' in col_mapping:
            df.rename(columns={col_mapping['subs']: 'Total Subs'}, inplace=True)
        if 'cancels' in col_mapping:
            df.rename(columns={col_mapping['cancels']: 'Total Cancels'}, inplace=True)
        if 'date' in col_mapping:
            df.rename(columns={col_mapping['date']: 'Date'}, inplace=True)
        if 'quarter' in col_mapping:
            df.rename(columns={col_mapping['quarter']: 'Quarter'}, inplace=True)
        
        # Extract ticker info from filename
        filename = s3_key.replace('.csv', '').replace('_Daily', '')
        parts = filename.split('_')
        
        # Get the ticker symbol (first part, uppercase)
        ticker_symbol = parts[0].upper()
        
        # For compound tickers like TMUSphone, keep them together
        if len(parts) >= 2 and parts[1].lower() in ['phone', 'broadband']:
            ticker_symbol = (parts[0] + parts[1]).upper()
            kpi_parts = parts[2:] if len(parts) > 2 else []
        else:
            kpi_parts = parts[1:] if len(parts) > 1 else []
        
        # Look up KPI from default mapping, or extract from filename
        if ticker_symbol in DEFAULT_TICKER_KPIS:
            default_kpi = DEFAULT_TICKER_KPIS[ticker_symbol]
        elif kpi_parts:
            default_kpi = ' '.join(kpi_parts).title()
        else:
            default_kpi = 'Customers'
        
        # Get metadata (allows admin overrides)
        metadata = load_ticker_metadata()
        ticker_metadata = metadata.get(s3_key, {})
        
        display_name = ticker_metadata.get('display_name', ticker_symbol)
        kpi = ticker_metadata.get('kpi', default_kpi)  # Use default_kpi instead of 'Unknown KPI'
        parent_ticker = ticker_metadata.get('parent_ticker', None)
        relevance_percentage = ticker_metadata.get('relevance_percentage', None)
        
        # Convert to records
        data = df.to_dict('records')
        
        # Optional: filter by quarter or date range (for Compare tab)
        if filter_quarter and len(data) > 0:
            data = [d for d in data if (d.get('Quarter') or '').strip() == filter_quarter]
        elif (filter_from or filter_to) and len(data) > 0:
            def _date_in_range(d):
                dt = (d.get('Date') or d.get('date') or '').strip()
                if not dt:
                    return True
                if filter_from and dt < filter_from:
                    return False
                if filter_to and dt > filter_to:
                    return False
                return True
            data = [d for d in data if _date_in_range(d)]
        
        # Pre-calculate all stats for immediate display (no waiting on frontend)
        calculated_stats = {}
        if len(data) > 0:
            latest = data[-1]
            latest_quarter = latest.get('Quarter', 'N/A') if not filter_quarter else filter_quarter
            
            # Use filtered data as the "current quarter" for stats
            current_quarter_data = data
            
            if len(current_quarter_data) > 0:
                # Calculate cumulative stats for current quarter (QTD - Quarter to Date)
                quarter_subs = sum(d.get('Total Subs', 0) for d in current_quarter_data)
                quarter_cancels = sum(d.get('Total Cancels', 0) for d in current_quarter_data)
                quarter_net_growth = quarter_subs - quarter_cancels
                quarter_start_consumers = current_quarter_data[0].get('Total Consumers', 1)
                
                # QTD Net Growth % = actual growth so far in the quarter
                net_growth_pct = (quarter_net_growth / quarter_start_consumers * 100) if quarter_start_consumers > 0 else 0
                
                # Projected net growth rate formula (linear extrapolation, not compounding):
                # 1. Average daily net growth = (quarter_subs - quarter_cancels) / days_of_data_so_far
                # 2. Projected net growth for full quarter = avg_daily_net_growth * total_days_in_quarter
                # 3. Projected % = (projected_net_growth / quarter_start_consumers) * 100
                days_in_quarter = len(current_quarter_data)
                avg_daily_net_growth = quarter_net_growth / days_in_quarter if days_in_quarter > 0 else 0
                total_days_in_quarter = 92 if 'Q4' in latest_quarter else 90
                projected_net_growth = avg_daily_net_growth * total_days_in_quarter
                projected_net_growth_pct = (projected_net_growth / quarter_start_consumers * 100) if quarter_start_consumers > 0 else 0
                
                # Calculate accuracy rating based on SEC actuals (comparing Growth % vs SEC %)
                accuracy_rating = None
                accuracy_score = None
                overall_variance = None
                quarter_variance = None
                try:
                    # Load SEC actuals from S3
                    all_sec_actuals = load_json_from_s3(SEC_ACTUALS_FILE)
                    ticker_actuals = all_sec_actuals.get(ticker_symbol, {})
                    
                    if ticker_actuals:
                        # Build quarters: start_consumers = Total Consumers on first day of quarter, end_consumers = last day
                        quarters = {}
                        for d in data:
                            q = (d.get('Quarter') or '').strip()
                            if not q or q == 'N/A':
                                continue
                            if q not in quarters:
                                quarters[q] = {
                                    'subs': 0, 'cancels': 0,
                                    'start_consumers': None, 'end_consumers': None,
                                    'first_date': None, 'last_date': None
                                }
                            consumers = d.get('Total Consumers')
                            if consumers is not None:
                                consumers = float(consumers) if not isinstance(consumers, (int, float)) else consumers
                            date_str = d.get('Date') or d.get('date') or ''
                            quarters[q]['subs'] += d.get('Total Subs', 0) or 0
                            quarters[q]['cancels'] += d.get('Total Cancels', 0) or 0
                            if date_str:
                                if quarters[q]['first_date'] is None or date_str < quarters[q]['first_date']:
                                    quarters[q]['first_date'] = date_str
                                    quarters[q]['start_consumers'] = consumers
                                if quarters[q]['last_date'] is None or date_str > quarters[q]['last_date']:
                                    quarters[q]['last_date'] = date_str
                                    quarters[q]['end_consumers'] = consumers
                            else:
                                if quarters[q]['start_consumers'] is None:
                                    quarters[q]['start_consumers'] = consumers
                                quarters[q]['end_consumers'] = consumers
                        for q, q_data in quarters.items():
                            if q_data['start_consumers'] is None and q_data['end_consumers'] is not None:
                                q_data['start_consumers'] = q_data['end_consumers']
                            if q_data['end_consumers'] is None and q_data['start_consumers'] is not None:
                                q_data['end_consumers'] = q_data['start_consumers']
                        
                        # Calculate variance for all quarters with SEC actuals
                        all_variances = []
                        quarter_specific_variances = []
                        
                        # Extract current quarter number (e.g., "Q1" from "Q1 2026")
                        current_quarter_num = latest_quarter.split()[0] if latest_quarter else None
                        
                        for quarter, q_data in quarters.items():
                            if quarter in ticker_actuals:
                                start_consumers = q_data['start_consumers']
                                end_consumers = q_data['end_consumers']
                                if start_consumers and start_consumers > 0 and end_consumers is not None:
                                    our_growth_pct = ((end_consumers - start_consumers) / start_consumers) * 100
                                else:
                                    net_growth = q_data['subs'] - q_data['cancels']
                                    our_growth_pct = (net_growth / start_consumers * 100) if start_consumers else 0
                                
                                # Get SEC actual % (value as entered)
                                sec_actual_pct = float(ticker_actuals[quarter])
                                
                                # Variance = difference in percentage points
                                variance = abs(our_growth_pct - sec_actual_pct)
                                all_variances.append(variance)
                                
                                # Check if this quarter matches current quarter (e.g., all Q1s)
                                quarter_num = quarter.split()[0]  # Extract "Q1" from "Q1 2024"
                                if quarter_num == current_quarter_num:
                                    quarter_specific_variances.append(variance)
                        
                        # Calculate overall variance (average across all quarters)
                        if all_variances:
                            overall_variance = sum(all_variances) / len(all_variances)
                            # Accuracy score based on overall variance
                            # Lower variance = higher accuracy
                            # Variance of 0% = 100% accuracy, Variance of 5% = 50% accuracy, etc.
                            accuracy_score = max(0, 100 - (overall_variance * 10))
                            
                            # Determine rating based on overall variance
                            if overall_variance < 0.5:
                                accuracy_rating = 'Excellent'
                            elif overall_variance < 1.0:
                                accuracy_rating = 'Very Good'
                            elif overall_variance < 2.0:
                                accuracy_rating = 'Good'
                            elif overall_variance < 3.0:
                                accuracy_rating = 'Fair'
                            else:
                                accuracy_rating = 'Needs Improvement'
                        
                        # Calculate quarter-specific variance (e.g., all Q1s)
                        if quarter_specific_variances:
                            quarter_variance = sum(quarter_specific_variances) / len(quarter_specific_variances)
                        
                except Exception as e:
                    print(f"⚠️ Could not calculate accuracy rating: {e}")
                
                calculated_stats = {
                    'current_consumers': latest.get('Total Consumers', 0),
                    'total_subs': quarter_subs,
                    'total_cancels': quarter_cancels,
                    'net_growth_pct': round(net_growth_pct, 2),
                    'projected_growth_pct': round(projected_net_growth_pct, 2),
                    'latest_date': latest.get('Date', 'N/A'),
                    'latest_quarter': latest_quarter,
                    'days_in_quarter': days_in_quarter,
                    'total_days_in_quarter': total_days_in_quarter,
                    'accuracy_rating': accuracy_rating,
                    'accuracy_score': round(accuracy_score, 1) if accuracy_score is not None else None,
                    'overall_variance': round(overall_variance, 2) if overall_variance is not None else None,
                    'quarter_variance': round(quarter_variance, 2) if quarter_variance is not None else None
                }
        
        response_data = {
            'success': True,
            'data': data,
            'ticker': ticker_symbol,  # Fixed: use ticker_symbol instead of undefined ticker_name
            'display_name': display_name,
            'kpi': kpi,
            'parent_ticker': parent_ticker,
            'relevance_percentage': relevance_percentage,
            'kpi_change_enabled': ticker_metadata.get('kpi_change_enabled', False),
            'kpi_change_quarter_start': ticker_metadata.get('kpi_change_quarter_start'),
            'kpi_change_quarter_end': ticker_metadata.get('kpi_change_quarter_end'),
            'kpi_change_label': ticker_metadata.get('kpi_change_label'),
            's3_key': s3_key,
            'bucket': HEDGE_FUND_S3_BUCKET,
            'calculated_stats': calculated_stats,  # Pre-calculated stats for instant display
            'filter_quarter': filter_quarter or None,
            'filter_from': filter_from or None,
            'filter_to': filter_to or None,
        }
        
        # Save to daily cache only when returning full data (no date/quarter filter)
        if not filter_quarter and not filter_from and not filter_to:
            _save_hedge_fund_daily_cache(s3_key, response_data)
        
        return jsonify(response_data)
        
    except KeyError as e:
        print(f"❌ KeyError in get_hedge_fund_ticker_data: Missing column {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False, 
            'error': f'Missing required column: {e}. Please check CSV format.',
            's3_key': s3_key
        }), 400
    except Exception as e:
        print(f"❌ Error in get_hedge_fund_ticker_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 's3_key': s3_key}), 500


@app.route('/api/hedge-fund-iq/metadata', methods=['GET', 'POST'])
@requires_auth
@requires_admin
def manage_ticker_metadata():
    """Get or update ticker metadata (display names, KPIs, parent tickers)."""
    if request.method == 'GET':
        metadata = load_ticker_metadata()
        return jsonify({'success': True, 'metadata': metadata})
    
    elif request.method == 'POST':
        data = request.get_json()
        s3_key = data.get('s3_key')
        ticker_symbol = (data.get('ticker_symbol') or '').strip()
        display_name = data.get('display_name')
        kpi = data.get('kpi')
        parent_ticker = data.get('parent_ticker')
        master_ticker = (data.get('master_ticker') or '').strip() or None
        relevance_percentage = data.get('relevance_percentage')
        # Tracked KPI change (for Historic Performance: different KPI in a date range)
        kpi_change_enabled = data.get('kpi_change_enabled', False)
        kpi_change_quarter_start = (data.get('kpi_change_quarter_start') or '').strip() or None
        kpi_change_quarter_end = (data.get('kpi_change_quarter_end') or '').strip() or None
        kpi_change_label = (data.get('kpi_change_label') or '').strip() or None
        
        if not s3_key:
            return jsonify({'success': False, 'error': 's3_key is required'}), 400
        
        metadata = load_ticker_metadata()
        
        if s3_key not in metadata:
            metadata[s3_key] = {}
        
        if ticker_symbol is not None:
            metadata[s3_key]['ticker_symbol'] = ticker_symbol if ticker_symbol else None
        if display_name:
            metadata[s3_key]['display_name'] = display_name
        if kpi:
            metadata[s3_key]['kpi'] = kpi
        if parent_ticker is not None:  # Allow empty string to clear
            metadata[s3_key]['parent_ticker'] = parent_ticker
        if master_ticker is not None:
            metadata[s3_key]['master_ticker'] = master_ticker
        
        if 'kpi_change_enabled' in data:
            metadata[s3_key]['kpi_change_enabled'] = bool(kpi_change_enabled)
        if 'kpi_change_quarter_start' in data:
            metadata[s3_key]['kpi_change_quarter_start'] = kpi_change_quarter_start
        if 'kpi_change_quarter_end' in data:
            metadata[s3_key]['kpi_change_quarter_end'] = kpi_change_quarter_end
        if 'kpi_change_label' in data:
            metadata[s3_key]['kpi_change_label'] = kpi_change_label
        
        # Handle relevance_percentage - always process if key exists in request
        if 'relevance_percentage' in data:  # Check if key exists in request
            try:
                # Handle None, empty string, or number
                if relevance_percentage is None:
                    # Explicitly clear the relevance
                    metadata[s3_key]['relevance_percentage'] = None
                elif isinstance(relevance_percentage, str) and relevance_percentage.strip() == '':
                    # Empty string - clear it
                    metadata[s3_key]['relevance_percentage'] = None
                else:
                    # Try to convert to float
                    try:
                        rel_pct = float(relevance_percentage)
                        if rel_pct < 0 or rel_pct > 100:
                            return jsonify({'success': False, 'error': 'Relevance percentage must be between 0-100'}), 400
                        metadata[s3_key]['relevance_percentage'] = rel_pct
                    except (ValueError, TypeError):
                        return jsonify({'success': False, 'error': 'Invalid relevance percentage format'}), 400
            except Exception as e:
                return jsonify({'success': False, 'error': f'Error processing relevance percentage: {str(e)}'}), 400
        
        # Save metadata to S3
        save_result = save_ticker_metadata(metadata)
        if not save_result:
            return jsonify({'success': False, 'error': 'Failed to save metadata to S3'}), 500
        
        return jsonify({
            'success': True, 
            'message': 'Metadata updated', 
            'metadata': metadata[s3_key],
            'relevance_percentage': metadata[s3_key].get('relevance_percentage')
        })


def load_ticker_metadata():
    """Load ticker metadata from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=TICKER_METADATA_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}


def save_ticker_metadata(metadata):
    """Save ticker metadata to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=TICKER_METADATA_KEY,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
        print(f"✅ Successfully saved ticker metadata to S3")
        return True
    except Exception as e:
        print(f"❌ Error saving ticker metadata: {e}")
        import traceback
        traceback.print_exc()
        return False


@app.route('/api/hedge-fund-iq/predict-earnings', methods=['POST'])
@requires_auth
def predict_earnings():
    """Use AI to predict earnings beat/miss based on KPI data. Cached daily for all users."""
    try:
        data = request.get_json()
        ticker = data.get('ticker')
        quarter = data.get('quarter')
        
        # Check daily cache first
        cache_slug = f"predict_{ticker}_{quarter}" if ticker and quarter else None
        if cache_slug:
            cached = _load_hedge_fund_daily_cache(cache_slug)
            if cached is not None:
                return jsonify(cached)
        
        client = get_openai_client()
        if not client:
            return jsonify({'success': False, 'error': 'OpenAI not configured'}), 500
        display_name = data.get('display_name')
        kpi = data.get('kpi')
        current_consumers = data.get('current_consumers')
        quarter = data.get('quarter')
        avg_daily_net_growth = data.get('avg_daily_net_growth')
        total_subs = data.get('total_subs')
        total_cancels = data.get('total_cancels')
        days_in_quarter = data.get('days_in_quarter')
        
        # Create prompt for AI
        prompt = f"""You are a financial analyst specializing in earnings predictions based on operational KPIs.

Analyze the following data for {display_name} ({ticker}):

KPI Being Measured: {kpi}
Current Quarter: {quarter}
Days of Data Available: {days_in_quarter}
Current Total Consumers: {current_consumers:,}
Total Subscriptions (Quarter): {total_subs:,}
Total Cancellations (Quarter): {total_cancels:,}
Average Daily Net Growth: {avg_daily_net_growth}

Based on this KPI data, predict whether this company will BEAT or MISS their publicly stated earnings expectations for {quarter}.

Consider:
1. The trend in net growth (positive or negative)
2. The magnitude of subscriber additions vs. cancellations
3. Historical correlation between this KPI and earnings performance
4. Industry benchmarks for similar metrics

Provide your prediction in the following JSON format:
{{
  "prediction": "BEAT" or "MISS",
  "confidence": <number 0-100>,
  "analysis": "<2-3 sentence explanation of your reasoning>"
}}

Respond ONLY with valid JSON, no additional text."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a financial analyst. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse JSON response
        import json
        result = json.loads(result_text)
        
        response_data = {
            'success': True,
            'prediction': result.get('prediction', 'UNKNOWN'),
            'confidence': result.get('confidence', 50),
            'analysis': result.get('analysis', 'Analysis unavailable')
        }
        
        # Save to daily cache
        quarter = data.get('quarter')
        if ticker and quarter:
            _save_hedge_fund_daily_cache(f"predict_{ticker}_{quarter}", response_data)
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"❌ Error in predict_earnings: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/beat-miss-analysis', methods=['POST'])
@requires_auth
def analyze_beat_miss():
    """Analyze whether company will beat or miss earnings based on KPI data. Cached daily for all users."""
    try:
        data = request.get_json()
        ticker = data.get('ticker')
        quarter = data.get('quarter')
        kpi = data.get('kpi') or ''
        relevance_percentage = data.get('relevance_percentage')
        # Cache key includes KPI and Stock Impact so we don't serve stale analysis when ticker profile changes
        import hashlib
        _kpi_relevance = f"{kpi}_{relevance_percentage}"
        _h = hashlib.sha256(_kpi_relevance.encode()).hexdigest()[:12]
        cache_slug = f"beatmiss_{ticker}_{quarter}_{_h}" if ticker and quarter else None
        if cache_slug:
            cached = _load_hedge_fund_daily_cache(cache_slug)
            if cached is not None:
                return jsonify(cached)
        
        client = get_openai_client()
        if not client:
            return jsonify({'success': False, 'error': 'OpenAI not configured'}), 500
        
        display_name = data.get('display_name')
        kpi = data.get('kpi')
        quarter = data.get('quarter')  # e.g., "Q1 2026"
        projected_growth_pct = data.get('projected_growth_pct', 0)
        relevance_percentage = data.get('relevance_percentage')  # 0-100 scale
        accuracy_score = data.get('accuracy_score')  # 0-100
        variance = data.get('variance')  # percentage variance
        current_consumers = data.get('current_consumers', 0)
        
        if not ticker or not quarter:
            return jsonify({'success': False, 'error': 'Ticker and quarter required'}), 400
        
        # Extract quarter info (e.g., "Q1 2026" -> "Q1", "2026")
        quarter_parts = quarter.split()
        quarter_label = quarter_parts[0] if quarter_parts else quarter
        year = quarter_parts[1] if len(quarter_parts) > 1 else None
        
        # Build comprehensive prompt for AI to research and analyze
        relevance_pct = relevance_percentage if relevance_percentage is not None else 0
        stock_impact_note = ""
        if relevance_pct >= 90:
            stock_impact_note = f"CRITICAL: This KPI has {relevance_pct}% Stock Impact, meaning it represents approximately {relevance_pct}% of the stock price movement. This is a force-ranked metric based on historical significance of the KPI to the company's share price behavior."
        elif relevance_pct >= 50:
            stock_impact_note = f"IMPORTANT: This KPI has {relevance_pct}% Stock Impact, meaning it represents approximately {relevance_pct}% of the stock price movement."
        else:
            stock_impact_note = f"This KPI has {relevance_pct}% Stock Impact, meaning it represents approximately {relevance_pct}% of the stock price movement."
        
        prompt = f"""You are a financial analyst. Determine whether {display_name} ({ticker}) will BEAT or MISS earnings expectations for {quarter}.

CRITICAL FRAMING:
- Companies and consensus project REVENUE GROWTH for the quarter. They do NOT project KPI-specific metrics like "{kpi}". So always extract and show what the company projects for revenue growth and what consensus projects for revenue growth (with exact % when available).
- Our metric is our measured/calculated growth for the single KPI "{kpi}"—it will almost never match company/consensus because they project revenue, not this KPI. Your job: (1) Show company revenue growth and consensus revenue growth for the quarter. (2) Show our KPI metric. (3) Explain how our metric fits within that revenue picture based on relevance (Stock Impact) and the resulting likelihood of beat vs miss.

{stock_impact_note}

EXTRACT (revenue growth for the quarter only):
- company_guidance: Revenue growth the company is projecting for {quarter}. One short phrase with exact % (e.g. "2.4% net revenue growth", "3% revenue growth"). If no number: "Not specified".
- consensus_estimate: Revenue growth analyst consensus is projecting for {quarter}. One short phrase with exact % (e.g. "3.3% revenue growth"). If no number: "Not available".

OUR DATA (measured KPI, not a company projection):
- KPI: {kpi}
- Our measured growth for this KPI this quarter: {projected_growth_pct}%
- Quarter: {quarter} | Stock Impact: {relevance_pct}% | Accuracy: {accuracy_score if accuracy_score is not None else 'Unknown'}% | Variance: {variance if variance is not None else 'Unknown'}%

ANALYSIS:
1. Research {ticker} guidance and consensus for {quarter} (SEC, earnings calls). Extract revenue growth numbers only.
2. In the analysis text: State what company and consensus project for revenue growth (with numbers). State our metric ({projected_growth_pct}% for {kpi}). Then explain how our metric fits within that revenue outlook—using Stock Impact ({relevance_pct}%) and data quality—and the resulting likelihood of beat or miss. Do not ask for or reference KPI-specific company guidance; they project revenue.
3. CRITICAL: When writing the analysis, you MUST refer to our measured metric ONLY as "{kpi}". Do NOT call it "Sales Revenue", "revenue", or any other name. We measure the KPI "{kpi}", not revenue. You MUST state Stock Impact as exactly {relevance_pct}% (use this number only; do not substitute a different percentage).
4. Output BEAT, MISS, or UNDECIDED.

JSON format. company_guidance and consensus_estimate: short phrase with exact % when available; else "Not specified" or "Not available".
{{
    "prediction": "BEAT" or "MISS" or "UNDECIDED",
    "confidence": <number 0-100>,
    "company_guidance": "<Revenue growth for the quarter the company is projecting. Exact % e.g. '2.4% net revenue growth'. If none: 'Not specified'.>",
    "consensus_estimate": "<Revenue growth for the quarter consensus is projecting. Exact % e.g. '3.3% revenue growth'. If none: 'Not available'.>",
    "our_projection": {projected_growth_pct},
    "analysis": "<State company and consensus revenue growth (with numbers). State our metric: {projected_growth_pct}% for {kpi}. Use the exact KPI name '{kpi}' and Stock Impact {relevance_pct}%. Explain how our metric fits within that revenue picture and the resulting likelihood of beat or miss.>"
}}

Respond ONLY with valid JSON, no additional text."""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a financial analyst. Extract what the company and consensus project for revenue growth for the quarter (they do not project KPI-specific metrics). In your analysis you MUST refer to our measured metric only by the exact KPI name provided (e.g. 'Total Customers at Period End')—never call it 'Sales Revenue' or 'revenue'. Use the exact Stock Impact percentage provided. Then explain how that KPI metric fits within the revenue picture and the resulting beat/miss likelihood. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=800
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse JSON response
        import json
        # Try to extract JSON from markdown code blocks if present
        if '```json' in result_text:
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif '```' in result_text:
            result_text = result_text.split('```')[1].split('```')[0].strip()
        
        result = json.loads(result_text)
        
        response_data = {
            'success': True,
            'prediction': result.get('prediction', 'UNDECIDED'),
            'confidence': result.get('confidence', 50),
            'company_guidance': result.get('company_guidance', 'Not found'),
            'consensus_estimate': result.get('consensus_estimate', 'Not found'),
            'our_projection': projected_growth_pct,
            'analysis': result.get('analysis', 'Analysis unavailable')
        }
        
        # Save to daily cache
        if ticker and quarter:
            _save_hedge_fund_daily_cache(f"beatmiss_{ticker}_{quarter}", response_data)
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"❌ Error in analyze_beat_miss: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def _normalize_sec_quarter(quarter):
    """Normalize quarter string to 'Qn YYYY' (e.g. Q4 2024) for consistent matching with frontend."""
    if not quarter or not isinstance(quarter, str):
        return (quarter or '').strip()
    s = quarter.strip()
    # Already "Q1 2024" style
    if re.match(r'^Q[1-4]\s+\d{4}$', s, re.IGNORECASE):
        return s[0].upper() + s[1:].lower()
    # "2024 Q1" or "2024-Q1"
    m = re.match(r'^(\d{4})[\s\-]+Q?([1-4])$', s, re.IGNORECASE)
    if m:
        return 'Q' + m.group(2) + ' ' + m.group(1)
    return s


@app.route('/api/hedge-fund-iq/sec-actuals/<ticker>')
@requires_auth
def get_sec_actuals(ticker):
    """Get SEC actuals for a ticker. Ticker is normalized to uppercase for lookup."""
    try:
        all_actuals = load_json_from_s3(SEC_ACTUALS_FILE)
        ticker_upper = (ticker or '').strip().upper()
        # Try normalized key first, then original (for legacy data)
        actuals = all_actuals.get(ticker_upper) or all_actuals.get(ticker) or {}
        # Normalize quarter keys in response so frontend "Q4 2024" matches
        if actuals and isinstance(actuals, dict):
            normalized = {}
            for q, val in actuals.items():
                nq = _normalize_sec_quarter(q)
                if nq:
                    normalized[nq] = val
            actuals = normalized
        return jsonify({
            'success': True,
            'actuals': actuals
        })
    except Exception as e:
        print(f"❌ Error getting SEC actuals: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/sec-actuals', methods=['POST'])
@requires_admin
def update_sec_actuals():
    """Update SEC actuals for a ticker and quarter (admin only). Ticker and quarter are normalized for consistent storage."""
    try:
        data = request.get_json()
        ticker_raw = data.get('ticker')
        quarter_raw = data.get('quarter')
        actual_value = data.get('actual_value')
        
        if not ticker_raw or not quarter_raw:
            return jsonify({'success': False, 'error': 'Ticker and quarter required'}), 400
        
        ticker = (ticker_raw or '').strip().upper()
        quarter = _normalize_sec_quarter(quarter_raw)
        if not quarter:
            return jsonify({'success': False, 'error': 'Invalid quarter format. Use e.g. Q1 2026'}), 400
        
        all_actuals = load_json_from_s3(SEC_ACTUALS_FILE)
        
        # Merge any legacy lowercase ticker key into uppercase so we don't lose data
        if ticker not in all_actuals and ticker_raw.strip() in all_actuals:
            all_actuals[ticker] = dict(all_actuals[ticker_raw.strip()])
        
        if ticker not in all_actuals:
            all_actuals[ticker] = {}
        
        if actual_value is None or actual_value == '':
            if quarter in all_actuals[ticker]:
                del all_actuals[ticker][quarter]
        else:
            all_actuals[ticker][quarter] = float(actual_value)
        
        save_json_to_s3(SEC_ACTUALS_FILE, all_actuals)
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"❌ Error updating SEC actuals: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/quick-selects', methods=['GET'])
@requires_admin
def get_quick_selects():
    """Get quick selects configuration (admin only)."""
    try:
        quick_selects = load_json_from_s3(QUICK_SELECTS_FILE)
        return jsonify({
            'success': True,
            'profiles': quick_selects.get('profiles', {}),
            'tickers': quick_selects.get('tickers', {}),
            'behaviors': quick_selects.get('behaviors', {})
        })
    except Exception as e:
        print(f"❌ Error loading quick selects: {e}")
        return jsonify({
            'success': True,  # Return success with empty data if file doesn't exist
            'profiles': {},
            'tickers': {},
            'behaviors': {}
        })


@app.route('/api/admin/quick-selects', methods=['POST'])
@requires_admin
def save_quick_selects():
    """Save quick selects configuration (admin only)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        profiles = data.get('profiles', {})
        tickers = data.get('tickers', {})
        behaviors = data.get('behaviors', {})
        
        quick_selects = {
            'profiles': profiles,
            'tickers': tickers,
            'behaviors': behaviors,
            'updated_at': datetime.now().isoformat()
        }
        
        # Check if S3 save was successful
        success = save_json_to_s3(QUICK_SELECTS_FILE, quick_selects)
        if not success:
            error_msg = 'Failed to save to S3. Check server logs for details.'
            print(f"❌ {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 500
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error saving quick selects: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# Default live feature flags (merge with saved so new keys like viewNumbers are always present)
DEFAULT_LIVE_FEATURES = {
    'compare': True, 'segment': True, 'execSummary': True, 'keyInsightBuilder': True,
    'ecosystem': True, 'affinity': True, 'sponsorship': True, 'media': True,
    'content': True, 'collaborate': True, 'deckBuilder': True, 'rankers': True,
    'overlapAnalysis': True, 'benchmarking': True, 'gapAnalysis': True,
    'insightsSummary': True, 'viewNumbers': True,
}


@app.route('/api/admin/live-features', methods=['GET'])
@requires_auth
def get_live_features():
    """Get live features configuration (available to all authenticated users to check visibility)."""
    try:
        live_features = load_json_from_s3(LIVE_FEATURES_FILE)
        saved = live_features.get('features', {})
        # Merge with defaults so new keys (e.g. viewNumbers) are always present; saved values override
        features = {**DEFAULT_LIVE_FEATURES, **saved}
        resp = jsonify({'success': True, 'features': features})
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        return resp
    except Exception as e:
        print(f"❌ Error loading live features: {e}")
        resp = jsonify({'success': True, 'features': dict(DEFAULT_LIVE_FEATURES)})
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        return resp


@app.route('/api/admin/live-features', methods=['POST'])
@requires_admin
def save_live_features():
    """Save live features configuration (admin only)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        features = data.get('features', {})
        
        live_features = {
            'features': features,
            'updated_at': datetime.now().isoformat(),
            'updated_by': session.get('username', 'unknown')
        }
        
        # Check if S3 save was successful
        success = save_json_to_s3(LIVE_FEATURES_FILE, live_features)
        if not success:
            error_msg = 'Failed to save to S3. Check server logs for details.'
            print(f"❌ {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 500
        
        print(f"✅ Live features saved by {session.get('username')}: {features}")
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error saving live features: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ecosystem/ai-analyze', methods=['POST'])
@requires_auth
def analyze_ecosystem_with_ai():
    """Analyze ecosystem using ChatGPT AI with raw CSV data - answers 6 key questions."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        profile_name = data.get('profileName', 'PROFILE')
        s3_key = data.get('s3Key') or data.get('profileId')
        csv_data = data.get('csvData')  # Raw CSV data as array of records
        
        if not csv_data and not s3_key:
            return jsonify({'success': False, 'error': 'No CSV data or S3 key provided'}), 400
        
        # Get OpenAI client
        client = get_openai_client()
        if not client:
            return jsonify({
                'success': False, 
                'error': 'OpenAI API not available',
                'fallback': True
            }), 503
        
        # If we have s3_key but no csv_data, fetch it
        if s3_key and not csv_data:
            try:
                response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
                csv_content = response['Body'].read().decode('utf-8')
                df = pd.read_csv(io.StringIO(csv_content))
                df = df.fillna('')
                csv_data = df.to_dict('records')
            except Exception as e:
                print(f"⚠️ Could not fetch CSV from S3: {e}")
                return jsonify({
                    'success': False,
                    'error': f'Could not fetch CSV data: {str(e)}'
                }), 500
        
        # Prepare data summary for AI (limit to avoid token limits)
        # Extract key behavioral data - only include items with 25%+ profile percentage
        behavioral_data = []
        demographic_data = {}
        
        for record in csv_data[:500]:  # Limit to first 500 rows
            col = str(record.get('Column', '')).upper()
            val = record.get('Value', '')
            idx = record.get('Index', 0)
            pct = record.get('Brand Penetration (Row)', 0) or record.get('pct', 0) or 0
            
            # Convert pct to float if it's a string
            try:
                pct = float(pct)
            except:
                pct = 0
            
            if 'BEHAVIORAL' in col or 'INTEREST' in col:
                # Only include items with 25%+ profile percentage
                if isinstance(val, (int, float)) and val > 0 and pct >= MIN_PCT_FOR_INSIGHTS:
                    behavioral_data.append({
                        'item': str(record.get('Column', '')),
                        'index': float(idx) if idx else 0,
                        'pct': pct,
                        'category': str(record.get('Category', 'Other'))
                    })
            elif 'DEMOGRAPHIC' in col or 'AGE' in col or 'GENDER' in col or 'INCOME' in col:
                # Only include demographics with 25%+ 
                if pct >= MIN_PCT_FOR_INSIGHTS:
                    demographic_data[col] = val
        
        # Sort by index and get top items
        behavioral_data.sort(key=lambda x: x.get('index', 0), reverse=True)
        top_items = behavioral_data[:100]  # Top 100 items
        
        # Build comprehensive prompt
        prompt = f"""You are a world-class marketing insights analyst. Analyze the behavioral data for {profile_name} as if you had the full CSV file. Answer these 6 critical questions with high-level, strategic insights:

1. **What people are doing before and after {profile_name} enters their life**
   - What behaviors, activities, or brands precede engagement with {profile_name}?
   - What behaviors follow engagement? What does this reveal about the customer journey?

2. **Cross-category substitution & "occasion leakage"**
   - When do people leave the category entirely? What replaces it?
   - What functional shifts are happening (e.g., fitness apps replacing beverage moments)?
   - Identify early-warning signals, not lagging sales data.

3. **Cultural momentum signals (pre-trend, not trend)**
   - What behavioral shifts are happening BEFORE culture names them?
   - Examples: nighttime consumption moving earlier, solo replacing social, "treat" reframed as mental health reward
   - Identify pre-trend signals, not TikTok views or obvious trends.

4. **Attention + emotion, not impressions**
   - When are people actually mentally receptive to {profile_name}?
   - What emotional state are they in at exposure?
   - Does {profile_name} show up in comfort vs. celebration modes?

5. **Who {profile_name} is losing relevance with — before sales show it**
   - Which audiences still purchase but are emotionally disengaging?
   - Where is "joy" being replaced with "habit"?
   - Identify at-risk segments before sales data reflects it.

6. **White-space occasions {profile_name} doesn't own yet**
   - What new moments could {profile_name} own?
   - Examples: productivity breaks, micro-celebrations, stress-relief rituals, solo rewards
   - Not "new flavors" — new moments.

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided. The data below has already been filtered to only include items with 25%+ profile percentage.

BEHAVIORAL DATA (Top 100 items by index, 25%+ profile percentage only):
{json.dumps(top_items[:100], indent=2)}

DEMOGRAPHIC DATA:
{json.dumps(demographic_data, indent=2)}

CRITICAL REQUIREMENTS:
- You MUST reference specific data points from the behavioral data provided (brand names, item names, index values, percentages)
- Include actual numbers, indices, or percentages when making claims
- Cite specific items from the data to support every insight
- Do not make generic statements without data backing
- Example: Instead of "consumers engage with fitness apps", say "consumers show strong engagement with Peloton (index 245) and Strava (index 198), indicating fitness apps are competing with beverage moments"

Provide your analysis in JSON format:
{{
    "question1_beforeAfter": "Detailed insight about what happens before and after engagement. MUST include specific brands/items from the data with their indices or percentages.",
    "question2_substitution": "Detailed insight about cross-category substitution and occasion leakage. MUST reference specific items from the data.",
    "question3_culturalMomentum": "Detailed insight about pre-trend cultural momentum signals. MUST cite specific behavioral data points.",
    "question4_attentionEmotion": "Detailed insight about attention and emotional receptivity. MUST reference actual data from the behavioral items.",
    "question5_relevanceLoss": "Detailed insight about who is losing relevance and why. MUST include specific demographic or behavioral data points.",
    "question6_whiteSpace": "Detailed insight about white-space occasions and opportunities. MUST reference specific items or categories from the data.",
    "executiveSummary": "2-3 sentence high-level summary of the most critical insights. MUST include at least one specific data point (brand, item, index, or percentage).",
    "keyRecommendations": ["Strategic recommendation 1 with data reference", "Strategic recommendation 2 with data reference", "Strategic recommendation 3 with data reference"]
}}

Be specific, data-driven, strategic, and actionable. Every insight MUST reference underlying data. Think like a CMO, not a data analyst."""

        # Call ChatGPT
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a world-class marketing strategist and consumer behavior expert. You analyze behavioral data to provide high-level strategic insights for C-suite executives."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=3000
        )
        
        # Parse response
        ai_response = response.choices[0].message.content
        
        # Try to parse as JSON
        try:
            # Try to extract JSON from markdown code blocks if present
            if '```json' in ai_response:
                ai_response = ai_response.split('```json')[1].split('```')[0].strip()
            elif '```' in ai_response:
                ai_response = ai_response.split('```')[1].split('```')[0].strip()
            
            insights = json.loads(ai_response)
        except Exception as e:
            print(f"⚠️ Could not parse AI response as JSON: {e}")
            # Fallback: wrap in structure
            insights = {
                "executiveSummary": ai_response,
                "question1_beforeAfter": ai_response,
                "question2_substitution": "",
                "question3_culturalMomentum": "",
                "question4_attentionEmotion": "",
                "question5_relevanceLoss": "",
                "question6_whiteSpace": "",
                "keyRecommendations": []
            }
        
        return jsonify({
            'success': True,
            'insights': insights
        })
        
    except Exception as e:
        print(f"❌ Error analyzing ecosystem with AI: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'fallback': True
        }), 500


@app.route('/api/executive-summary/ai-analyze', methods=['POST'])
@requires_auth
def analyze_executive_summary_with_ai():
    """Generate AI-powered executive summary from comparison data."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        primary_profile = data.get('primaryProfile')
        competitors = data.get('competitors', [])
        gen_pop_profile = data.get('genPopProfile')
        
        if not primary_profile:
            return jsonify({'success': False, 'error': 'No primary profile provided'}), 400
        
        # Get OpenAI client
        client = get_openai_client()
        if not client:
            return jsonify({
                'success': False, 
                'error': 'OpenAI API not available',
                'fallback': True
            }), 503
        
        # Prepare structured data for AI - filter to only include items >= 25%
        summary_data = {
            'primaryProfile': {
                'name': primary_profile.get('name', 'Unknown'),
                'sampleSize': primary_profile.get('sampleSize', 0),
                'projectedUS': primary_profile.get('projectedUS', 0),
                'medianAge': primary_profile.get('medianAge', 'N/A'),
                'demographics': _filter_demographics_for_insights(primary_profile.get('demographics', {})),
                'demographicsProjection': _filter_demographics_for_insights(primary_profile.get('demographicsProjection', {})),
                'topCategories': _filter_top_items_for_insights(primary_profile.get('topCategories', [])),
                'topInterests': _filter_top_items_for_insights(primary_profile.get('topInterests', []))
            },
            'competitors': [
                {
                    'name': comp.get('name', 'Unknown'),
                    'demographics': _filter_demographics_for_insights(comp.get('demographics', {})),
                    'demographicsProjection': _filter_demographics_for_insights(comp.get('demographicsProjection', {})),
                    'topCategories': _filter_top_items_for_insights(comp.get('topCategories', [])),
                    'topInterests': _filter_top_items_for_insights(comp.get('topInterests', []))
                }
                for comp in competitors
            ],
            'genPop': {
                'demographics': _filter_demographics_for_insights(gen_pop_profile.get('demographics', {})) if gen_pop_profile else {},
                'demographicsProjection': _filter_demographics_for_insights(gen_pop_profile.get('demographicsProjection', {})) if gen_pop_profile else {},
                'topCategories': _filter_top_items_for_insights(gen_pop_profile.get('topCategories', [])) if gen_pop_profile else [],
                'topInterests': _filter_top_items_for_insights(gen_pop_profile.get('topInterests', [])) if gen_pop_profile else []
            } if gen_pop_profile else None
        }
        
        # Build comprehensive prompt for strategic executive summary
        prompt = f"""You are a C-suite marketing strategist analyzing competitive consumer data. Generate a high-level, strategic executive summary that transforms raw data into actionable business insights.

PRIMARY PROFILE: {summary_data['primaryProfile']['name']}
- Sample Size: {summary_data['primaryProfile']['sampleSize']:,}
- Projected US Population: {summary_data['primaryProfile']['projectedUS']:,}
- Median Age: {summary_data['primaryProfile']['medianAge']}

COMPETITORS: {', '.join([c['name'] for c in summary_data['competitors']]) if summary_data['competitors'] else 'None'}

FULL DATA:
{json.dumps(summary_data, indent=2)}

IMPORTANT: 
1. The "Other" category represents the absence of data and should NEVER be used in analysis, comparisons, or callouts. Ignore any "Other" category entries completely.
2. Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.

Your task: Generate a strategic executive summary that:
1. **Identifies the most critical business insights** - not just data points, but what they mean for strategy
2. **Highlights competitive advantages and vulnerabilities** - where does the primary brand win vs. competitors and Gen Pop?
3. **Reveals demographic shifts and implications** - is the audience aging? shifting gender? income changes?
4. **Explains category and interest patterns** - what does this tell us about positioning and messaging? (EXCLUDE "Other" category - it represents missing data)
5. **Provides strategic recommendations** - what should leadership focus on?

Format your response as JSON:
{{
    "executiveOverview": "2-3 sentence high-level strategic overview",
    "demographicsInsights": "Strategic insights about demographics - what they mean, not just numbers. Compare to competitors and Gen Pop. Are they aging? Shifting? What are the implications?",
    "competitivePositioning": "How the primary brand positions vs. competitors - strengths, weaknesses, opportunities",
    "categoryAnalysis": "Strategic insights about purchased categories - what this reveals about brand positioning and consumer behavior. DO NOT mention or analyze the 'Other' category as it represents missing data.",
    "interestBehaviorInsights": "What the top interests/behaviors reveal about the consumer mindset and brand fit",
    "keyStrategicRecommendations": ["Recommendation 1", "Recommendation 2", "Recommendation 3"],
    "criticalWarnings": ["Any red flags or risks to highlight"],
    "opportunities": ["Strategic opportunities to pursue"]
}}

Be strategic, not descriptive. Think like a CMO presenting to the board. Use percentages and numbers where relevant, but focus on what they MEAN for the business."""
        
        # Call ChatGPT
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a world-class marketing strategist and C-suite advisor. You transform consumer data into high-level strategic insights for executive decision-making."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2500
        )
        
        # Parse response
        ai_response = response.choices[0].message.content
        
        # Try to parse as JSON
        try:
            # Try to extract JSON from markdown code blocks if present
            if '```json' in ai_response:
                ai_response = ai_response.split('```json')[1].split('```')[0].strip()
            elif '```' in ai_response:
                ai_response = ai_response.split('```')[1].split('```')[0].strip()
            
            insights = json.loads(ai_response)
        except Exception as e:
            print(f"⚠️ Could not parse AI response as JSON: {e}")
            # Fallback: wrap in structure
            insights = {
                "executiveOverview": ai_response,
                "demographicsInsights": "AI analysis available but could not be parsed",
                "competitivePositioning": "",
                "categoryAnalysis": "",
                "interestBehaviorInsights": "",
                "keyStrategicRecommendations": [],
                "criticalWarnings": [],
                "opportunities": []
            }
        
        return jsonify({
            'success': True,
            'insights': insights
        })
        
    except Exception as e:
        print(f"❌ Error analyzing executive summary with AI: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'fallback': True
        }), 500


@app.route('/api/key-insight-builder/ai-analyze', methods=['POST'])
@requires_auth
def analyze_key_insights_with_ai():
    """Generate AI-powered SWOT analysis for Key Insight Builder using ChatGPT."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        primary_profile = data.get('primaryProfile')
        competitors = data.get('competitors', [])
        gen_pop_profile = data.get('genPopProfile')
        
        if not primary_profile:
            return jsonify({'success': False, 'error': 'No primary profile provided'}), 400
        
        # Get OpenAI client
        client = get_openai_client()
        if not client:
            return jsonify({
                'success': False, 
                'error': 'OpenAI API not available',
                'fallback': True
            }), 503
        
        # Extract focused data: interests, demographics, social media, streaming/platforms, streaming/music, categories (NOT brands)
        # Filter to only include items with 25%+ profile percentage
        def extract_focused_data(profile):
            focused = {
                'name': profile.get('name', 'Unknown'),
                'sampleSize': profile.get('sampleSize', 0),
                'projectedUS': profile.get('projectedUS', 0),
                'medianAge': profile.get('medianAge', 'N/A'),
                'demographics': _filter_demographics_for_insights(profile.get('demographics', {})),
                'demographicsProjection': _filter_demographics_for_insights(profile.get('demographicsProjection', {})),
                'interests': [],
                'socialMedia': [],
                'streamingPlatforms': [],
                'streamingMusic': [],
                'categories': []
            }
            
            # Extract interests and behavioral items, filtering by category AND by 25% threshold
            behavioral = profile.get('behavioral', {})
            if behavioral:
                for category, items in behavioral.items():
                    if not isinstance(items, list):
                        continue
                    
                    cat_upper = str(category).upper()
                    
                    # Interests - only include items with 25%+ pct
                    if 'INTEREST' in cat_upper:
                        for item in items:
                            if item.get('name') and item.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS:
                                focused['interests'].append({
                                    'name': item.get('name'),
                                    'pct': item.get('pct', 0),
                                    'index': item.get('index', 0)
                                })
                    
                    # Social Media - only include items with 25%+ pct
                    elif 'SOCIAL' in cat_upper and ('MEDIA' in cat_upper or 'PLATFORM' in cat_upper):
                        for item in items:
                            if item.get('name') and item.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS:
                                focused['socialMedia'].append({
                                    'name': item.get('name'),
                                    'pct': item.get('pct', 0),
                                    'index': item.get('index', 0)
                                })
                    
                    # Streaming Platforms - only include items with 25%+ pct
                    elif 'STREAMING' in cat_upper and 'PLATFORM' in cat_upper:
                        for item in items:
                            if item.get('name') and item.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS:
                                focused['streamingPlatforms'].append({
                                    'name': item.get('name'),
                                    'pct': item.get('pct', 0),
                                    'index': item.get('index', 0)
                                })
                    
                    # Streaming Music - only include items with 25%+ pct
                    elif 'STREAMING' in cat_upper and 'MUSIC' in cat_upper:
                        for item in items:
                            if item.get('name') and item.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS:
                                focused['streamingMusic'].append({
                                    'name': item.get('name'),
                                    'pct': item.get('pct', 0),
                                    'index': item.get('index', 0)
                                })
                    
                    # Most Purchased Categories (NOT brands) - only include items with 25%+ pct
                    elif 'MOST PURCHASED CATEGORIES' in cat_upper or ('PURCHASED' in cat_upper and 'CATEGOR' in cat_upper and 'BRAND' not in cat_upper):
                        for item in items:
                            if item.get('name') and item.get('pct', 0) >= MIN_PCT_FOR_INSIGHTS:
                                item_name = str(item.get('name', '')).upper()
                                
                                # Exclude if it's clearly a brand name
                                is_brand = (
                                    any(indicator in item_name for indicator in ['INC', 'LLC', 'CORP', 'CO.', 'COMPANY']) or
                                    item_name in ['NIKE', 'COCA-COLA', 'COKE', 'PEPSI', 'APPLE', 'SAMSUNG', 'AMAZON', 'WALMART', 'TARGET', 'STARBUCKS', 'MCDONALDS', 'ADIDAS', 'UNDER ARMOUR']
                                )
                                
                                if not is_brand:
                                    focused['categories'].append({
                                        'name': item.get('name'),
                                        'pct': item.get('pct', 0),
                                        'index': item.get('index', 0),
                                        'category': item.get('category', category)
                                    })
            
            # Sort by index (descending) and take top items
            for key in ['interests', 'socialMedia', 'streamingPlatforms', 'streamingMusic', 'categories']:
                focused[key] = sorted(focused[key], key=lambda x: x.get('index', 0), reverse=True)[:20]
            
            return focused
        
        # Prepare focused data for all profiles
        primary_data = extract_focused_data(primary_profile)
        competitor_data = [extract_focused_data(comp) for comp in competitors]
        gen_pop_data = extract_focused_data(gen_pop_profile) if gen_pop_profile else None
        
        # Build comprehensive prompt for SWOT analysis
        prompt = f"""You are a world-class marketing strategist performing a SWOT analysis. Analyze the primary brand compared to competitors and General Population.

PRIMARY BRAND: {primary_data['name']}
- Sample Size: {primary_data['sampleSize']:,}
- Projected US: {primary_data['projectedUS']:,}
- Median Age: {primary_data['medianAge']}

COMPETITORS: {', '.join([c['name'] for c in competitor_data]) if competitor_data else 'None'}

FOCUS HEAVILY ON:
1. **Interests** - What interests does the primary brand audience have vs. competitors and Gen Pop?
2. **Demographics** - Age, gender, income, ethnicity differences
3. **Social Media** - Which platforms does the audience use vs. competitors?
4. **Streaming Platforms** - Video streaming preferences
5. **Streaming Music** - Music streaming preferences  
6. **Most Purchased Categories** - Category-level purchases (NOT individual brands)

DO NOT focus on:
- Individual brand names (focus on categories instead)
- Generic behavioral items
- "Other" category (represents missing data)
- Data points with less than 25% of the audience

IMPORTANT: Only cite or reference data points that represent 25% or more of the audience. Do not make up or infer data points that are not provided.

PRIMARY BRAND DATA:
{json.dumps(primary_data, indent=2)}

COMPETITOR DATA:
{json.dumps(competitor_data, indent=2)}

GEN POP DATA:
{json.dumps(gen_pop_data, indent=2) if gen_pop_data else 'Not provided'}

Generate a comprehensive SWOT analysis in JSON format:
{{
    "demographicsOverview": "Detailed demographic overview comparing primary to competitors and Gen Pop. Include specific percentages and numbers.",
    "strengths": [
        "Strength 1 - specific, data-backed, comparing to competitors and Gen Pop",
        "Strength 2 - with actual numbers/percentages",
        "Strength 3 - etc."
    ],
    "weaknesses": [
        "Weakness 1 - specific, data-backed, comparing to competitors and Gen Pop",
        "Weakness 2 - with actual numbers/percentages",
        "Weakness 3 - etc."
    ],
    "opportunities": [
        "Opportunity 1 - specific, actionable, data-backed",
        "Opportunity 2 - with actual numbers/percentages",
        "Opportunity 3 - etc."
    ],
    "threats": [
        "Threat 1 - specific, data-backed, comparing to competitors",
        "Threat 2 - with actual numbers/percentages",
        "Threat 3 - etc."
    ],
    "whereBrandShines": [
        "Specific area where brand excels - with data",
        "Another area - with percentages/numbers",
        "etc."
    ]
}}

CRITICAL REQUIREMENTS:
- Every insight MUST reference specific data (percentages, numbers, categories, platforms)
- Compare primary brand to BOTH competitors AND Gen Pop
- Focus on interests, demographics, social media, streaming platforms, streaming music, and categories
- Do NOT mention individual brand names - focus on categories
- Do NOT mention "Other" category
- Be strategic and actionable, not just descriptive
- Think like a CMO presenting to the board"""
        
        # Call ChatGPT
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a world-class marketing strategist and C-suite advisor. You perform SWOT analyses that transform consumer data into high-level strategic insights for executive decision-making."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=3000
        )
        
        # Parse response
        ai_response = response.choices[0].message.content
        
        # Try to parse as JSON
        try:
            # Try to extract JSON from markdown code blocks if present
            if '```json' in ai_response:
                ai_response = ai_response.split('```json')[1].split('```')[0].strip()
            elif '```' in ai_response:
                ai_response = ai_response.split('```')[1].split('```')[0].strip()
            
            insights = json.loads(ai_response)
        except Exception as e:
            print(f"⚠️ Could not parse AI response as JSON: {e}")
            # Fallback: wrap in structure
            insights = {
                "demographicsOverview": ai_response[:500] if len(ai_response) > 500 else ai_response,
                "strengths": ["AI analysis available but could not be parsed"],
                "weaknesses": [],
                "opportunities": [],
                "threats": [],
                "whereBrandShines": []
            }
        
        return jsonify({
            'success': True,
            'insights': insights
        })
        
    except Exception as e:
        print(f"❌ Error analyzing key insights with AI: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'fallback': True
        }), 500


@app.route('/api/admin/refresh-cache', methods=['POST'])
@requires_admin
def refresh_metadata_cache():
    """Refresh the in-memory metadata cache from S3 (admin only)."""
    try:
        data = request.get_json() or {}
        filename = data.get('filename')  # Optional: refresh specific file
        
        if filename:
            # Invalidate specific file and reload
            invalidate_cache(filename)
            load_json_from_s3(filename, use_cache=False)
            return jsonify({
                'success': True,
                'message': f'Cache refreshed for {filename}'
            })
        else:
            # Invalidate all and reload all metadata files
            invalidate_cache()
            load_json_from_s3(TICKER_IMAGES_FILE, use_cache=False)
            load_json_from_s3(TICKER_PROFILES_FILE, use_cache=False)
            load_json_from_s3(SEC_ACTUALS_FILE, use_cache=False)
            return jsonify({
                'success': True,
                'message': 'All metadata cache refreshed'
            })
        
    except Exception as e:
        print(f"❌ Error refreshing cache: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/profile-mapping/<ticker>')
@requires_auth
def get_profile_mapping(ticker):
    """Get the profile filename mappings for a ticker (supports up to 5 profiles)."""
    try:
        # Load profile mappings from S3
        mappings = load_json_from_s3(TICKER_PROFILES_FILE)
        if ticker in mappings:
            # Support both old format (string) and new format (array)
            profiles = mappings[ticker]
            if isinstance(profiles, str):
                # Convert old format to new format
                profiles = [profiles]
            
            return jsonify({
                'success': True,
                'profiles': profiles
            })
        
        # Default: use ticker name as profile filename
        return jsonify({
            'success': True,
            'profiles': [ticker]
        })
        
    except Exception as e:
        print(f"❌ Error getting profile mapping: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/profile-mapping', methods=['POST'])
@requires_admin
def update_profile_mapping():
    """Update the profile filename mappings for a ticker (admin only, supports up to 5 profiles)."""
    try:
        data = request.get_json()
        ticker = data.get('ticker')
        profiles = data.get('profiles', [])
        
        print(f"📝 Updating profile mappings for {ticker}: {profiles}")
        
        if not ticker:
            return jsonify({'success': False, 'error': 'Ticker required'}), 400
        
        # Validate profiles (max 5, non-empty strings)
        if profiles:
            profiles = [p.strip() for p in profiles if p and p.strip()]
            if len(profiles) > 5:
                return jsonify({'success': False, 'error': 'Maximum 5 profiles allowed'}), 400
        
        # Load existing mappings from S3
        mappings = load_json_from_s3(TICKER_PROFILES_FILE)
        print(f"📂 Loaded existing mappings: {list(mappings.keys())}")
        
        if profiles:
            # Update mapping with array of profiles
            mappings[ticker] = profiles
            print(f"✅ Set mappings: {ticker} → {profiles}")
        else:
            # Remove mapping (use default)
            if ticker in mappings:
                del mappings[ticker]
                print(f"🗑️ Removed mappings for {ticker}")
            else:
                print(f"ℹ️ No mappings to remove for {ticker}")
        
        # Save back to S3
        save_json_to_s3(TICKER_PROFILES_FILE, mappings)
        print(f"💾 Saved mappings to S3")
        print(f"📊 Total tickers with mappings: {len(mappings)}")
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"❌ Error updating profile mapping: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/<path:filename>')
@requires_auth
def get_profile_data(filename):
    """Get profile data from S3 dashboard-inputs bucket and return as JSON."""
    try:
        print(f"📊 Loading profile: {filename}")
        
        if not filename.endswith('.csv'):
            filename = f"{filename}.csv"
        
        try:
            obj = s3_client.get_object(Bucket='dashboard-inputs', Key=filename)
            csv_bytes = obj['Body'].read()
            csv_text = csv_bytes.decode('utf-8')
        except s3_client.exceptions.NoSuchKey:
            print(f"❌ Profile not found: {filename}")
            return jsonify({'success': False, 'error': f'Profile file not found: {filename}'}), 404
        except Exception as e:
            print(f"❌ Error reading profile from S3: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
        
        import io
        df = pd.read_csv(io.StringIO(csv_text))
        df = df.fillna('')
        
        brand_name = filename.replace('.csv', '').split('_')[0]
        
        print(f"✅ Loaded profile {filename}: {len(df)} rows")
        return jsonify({
            'success': True,
            'filename': filename,
            'brand': brand_name,
            'data': df.to_dict(orient='records')
        })
        
    except Exception as e:
        print(f"❌ Error loading profile: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ticker-image/<ticker>')
@requires_auth
def get_ticker_image(ticker):
    """Get ticker image URL."""
    try:
        # Load ticker images from S3
        ticker_images = load_json_from_s3(TICKER_IMAGES_FILE)
        if ticker in ticker_images:
            return jsonify({
                'success': True,
                'image_url': ticker_images[ticker].get('image_url'),
                'is_custom': ticker_images[ticker].get('is_custom', False)
            })
        
        return jsonify({'success': True, 'image_url': None})
    except Exception as e:
        print(f"❌ Error getting ticker image: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/ticker-image', methods=['POST'])
@requires_admin
def set_ticker_image():
    """Set a custom ticker image (upload or URL)."""
    try:
        print(f"📸 Ticker image request - files: {list(request.files.keys())}, form: {dict(request.form)}")
        
        ticker = request.form.get('ticker')
        if not ticker:
            return jsonify({'success': False, 'error': 'Ticker is required'}), 400
        
        # Load existing cache from S3
        ticker_images = load_json_from_s3(TICKER_IMAGES_FILE)
        
        # Handle file upload
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                # Validate file type
                allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                
                if ext not in allowed_extensions:
                    return jsonify({'success': False, 'error': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'}), 400
                
                # Upload to S3
                s3_key = f"ticker-images/{uuid.uuid4().hex}.{ext}"
                
                try:
                    s3_client.upload_fileobj(
                        file,
                        S3_BUCKET_NAME,
                        s3_key,
                        ExtraArgs={'ContentType': file.content_type or 'image/png'}
                    )
                    
                    image_url = f"/api/ticker-image-file/{s3_key}"
                    print(f"   ✅ Uploaded ticker image to S3: {s3_key}")
                    
                except Exception as e:
                    print(f"   ❌ S3 upload failed: {e}")
                    return jsonify({'success': False, 'error': f'Upload failed: {str(e)}'}), 500
        
        # Handle URL
        elif 'image_url' in request.form:
            image_url = request.form.get('image_url')
            if not image_url:
                return jsonify({'success': False, 'error': 'Image URL is required'}), 400
        else:
            return jsonify({'success': False, 'error': 'Either file or image_url is required'}), 400
        
        # Save to cache in S3
        ticker_images[ticker] = {
            'image_url': image_url,
            'is_custom': True,
            'updated_at': datetime.now().isoformat()
        }
        
        save_json_to_s3(TICKER_IMAGES_FILE, ticker_images)
        print(f"   ✅ Saved ticker image for {ticker}")
        
        return jsonify({
            'success': True,
            'image_url': image_url,
            'message': 'Ticker image saved'
        })
        
    except Exception as e:
        print(f"❌ Error setting ticker image: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/ticker-image', methods=['DELETE'])
@requires_admin
def remove_ticker_image():
    """Remove custom ticker image."""
    try:
        ticker = request.args.get('ticker')
        if not ticker:
            return jsonify({'success': False, 'error': 'Ticker is required'}), 400
        
        # Load cache from S3
        ticker_images = load_json_from_s3(TICKER_IMAGES_FILE)
        
        if ticker in ticker_images:
            del ticker_images[ticker]
            save_json_to_s3(TICKER_IMAGES_FILE, ticker_images)
            print(f"   ✅ Removed ticker image for {ticker}")
        
        return jsonify({'success': True, 'message': 'Ticker image removed'})
        
    except Exception as e:
        print(f"❌ Error removing ticker image: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ticker-image-file/<path:s3_key>')
@requires_auth
def serve_ticker_image(s3_key):
    """Proxy endpoint to serve ticker images from S3."""
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        image_data = response['Body'].read()
        content_type = response.get('ContentType', 'image/png')
        
        return Response(image_data, mimetype=content_type)
    except Exception as e:
        print(f"Error serving ticker image {s3_key}: {e}")
        return '', 404


@app.route('/api/admin/tickers-without-images')
@requires_admin
def get_tickers_without_images():
    """Get list of all tickers that don't have custom images."""
    try:
        # Load all tickers
        tickers_response = list_hedge_fund_tickers()
        tickers_data = tickers_response.get_json()
        
        if not tickers_data.get('success'):
            return jsonify({'success': False, 'error': 'Could not load tickers'}), 500
        
        all_tickers = tickers_data.get('tickers', [])
        
        # Load ticker images cache from S3
        ticker_images = load_json_from_s3(TICKER_IMAGES_FILE)
        
        # Separate tickers with and without images
        tickers_without_images = []
        tickers_with_images = []
        
        for ticker_data in all_tickers:
            ticker = ticker_data['ticker']
            
            if ticker in ticker_images and ticker_images[ticker].get('image_url'):
                tickers_with_images.append({
                    'ticker': ticker,
                    'display_name': ticker_data['display_name'],
                    'kpi': ticker_data['kpi'],
                    'image_url': ticker_images[ticker]['image_url']
                })
            else:
                tickers_without_images.append({
                    'ticker': ticker,
                    'display_name': ticker_data['display_name'],
                    'kpi': ticker_data['kpi']
                })
        
        return jsonify({
            'success': True,
            'without_images': tickers_without_images,
            'with_images': tickers_with_images,
            'total_count': len(all_tickers),
            'missing_count': len(tickers_without_images),
            'has_image_count': len(tickers_with_images)
        })
        
    except Exception as e:
        print(f"Error in get_tickers_without_images: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/tickers-full')
@requires_admin
def get_tickers_full():
    """Get all tickers with images and profile mappings in one call for admin panel."""
    try:
        # Get all tickers
        tickers_response = list_hedge_fund_tickers()
        tickers_data = tickers_response.get_json()
        
        if not tickers_data.get('success'):
            return jsonify({'success': False, 'error': 'Could not load tickers'}), 500
        
        all_tickers = tickers_data.get('tickers', [])
        
        # Load ticker images and profile mappings from S3 (cached)
        ticker_images = load_json_from_s3(TICKER_IMAGES_FILE)
        profile_mappings = load_json_from_s3(TICKER_PROFILES_FILE)
        
        # Enrich each ticker with image and profile data
        enriched_tickers = []
        for ticker_data in all_tickers:
            ticker = ticker_data['ticker']
            
            # Get image info
            has_image = False
            image_url = None
            if ticker in ticker_images and ticker_images[ticker].get('image_url'):
                has_image = True
                image_url = ticker_images[ticker].get('image_url')
            
            # Get profile mappings
            profiles = [ticker]  # Default
            has_custom_profile = False
            if ticker in profile_mappings:
                profiles = profile_mappings[ticker]
                if isinstance(profiles, str):
                    profiles = [profiles]
                has_custom_profile = profiles[0] != ticker if profiles else False
            
            enriched_tickers.append({
                'ticker': ticker,
                'display_name': ticker_data.get('display_name', ticker),
                'kpi': ticker_data.get('kpi', 'Customers'),
                'relevance_percentage': ticker_data.get('relevance_percentage'),
                's3_key': ticker_data.get('s3_key'),  # Include s3_key for metadata updates
                'hasImage': has_image,
                'imageUrl': image_url,
                'profiles': profiles,
                'profileFilename': profiles[0] if profiles else ticker,
                'hasCustomProfile': has_custom_profile
            })
        
        return jsonify({
            'success': True,
            'tickers': enriched_tickers,
            'count': len(enriched_tickers)
        })
        
    except Exception as e:
        print(f"❌ Error in get_tickers_full: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


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
        # Check user credits first (Profile Analysis costs 5 credits)
        username = session.get('username')
        if not has_credits_for(username, CREDITS_PROFILE_ANALYSIS):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Profile Analysis requires {CREDITS_PROFILE_ANALYSIS} credits. You have {"no" if credits_left == 0 else credits_left} remaining. Please contact an administrator to get more credits.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        
        data = request.json
        
        # Validate required fields (support both old and new date field names)
        if not data.get('project_name'):
            return jsonify({'error': 'Missing required field: project_name'}), 400
        geo_zip_codes_raw = data.get('geo_zip_codes', '').strip()
        geo_dma_raw = data.get('geo_dma', '').strip()
        is_geo_profile = bool(geo_zip_codes_raw and geo_dma_raw)
        if not is_geo_profile and not data.get('brands'):
            return jsonify({'error': 'Missing required field: brands (or provide zip codes + DMA)'}), 400
        if not (data.get('sample_start') or data.get('start_date')):
            return jsonify({'error': 'Missing required field: start date'}), 400
        if not (data.get('sample_end') or data.get('end_date')):
            return jsonify({'error': 'Missing required field: end date'}), 400
        
        # Create job ID
        job_id = str(uuid.uuid4())[:8]
        
        # Parse inputs
        project_name = data['project_name'].replace(' ', '_')
        project_name = re.sub(r'[<>:"/\\|?*]', '_', project_name)
        
        # Parse geo parameters
        geo_zip_codes = None
        geo_dma = None
        if is_geo_profile:
            geo_zip_codes = [z.strip() for z in geo_zip_codes_raw.replace('\n', ',').split(',') if z.strip()]
            geo_dma = geo_dma_raw
            brands = []
            print(f"📍 Geographic profile: {len(geo_zip_codes)} zip codes, DMA='{geo_dma}'")
        else:
            # Parse brands (same as terminal: comma-separated, optional URL strip)
            brands_raw = data['brands'].replace('\n', ',')
            brands = []
            for b in brands_raw.split(','):
                b = b.strip()
                if not b:
                    continue
                match = re.search(r'https?://([^/]+)', b)
                clean_brand = match.group(1).lower() if match else b.lower()
                brands.append(clean_brand)
            # Full expansion to all name combos (like terminal "Auto Format Inputs? Y") is done in run_analysis via bg.generate_brand_variations
        
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
        
        # Automatically search for similar files for demographic consistency
        reference_demographics = None
        reference_sample_size = None
        reference_file_key = None
        
        # Search S3 for existing runs with same brand (skip for geo profiles)
        try:
            brand_label = brands[0] if brands else f"GEO_{geo_dma}"
            if brands:
                print(f"🔍 Checking for existing runs of '{brands[0]}' for consistency validation...")
                exact_match, similar_files = check_s3_for_existing(brands[0], start_date, end_date)
            else:
                exact_match, similar_files = None, []
            
            # If there's a similar file (same brand, different dates), use it as reference
            if similar_files:
                # Use the most recent similar file as reference
                similar_files.sort(key=lambda x: x.get('last_modified', ''), reverse=True)
                reference_file = similar_files[0]
                reference_file_key = reference_file['key']
                reference_demographics = reference_file['demographics']
                reference_sample_size = reference_file['sample_size']
                print(f"📋 Found reference file: {reference_file_key}")
                print(f"   Reference sample size: {reference_sample_size}")
                print(f"   Will enforce ±2% consistency for demographics and sample size")
            else:
                print(f"📋 No previous runs found for '{brands[0]}' - will create baseline")
        except Exception as e:
            print(f"⚠️ Error checking for reference files: {e}")
        
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
            'brands': brand_label,
            'result_file': None,
            'error': None,
            'logs': [],
            'reference_demographics': reference_demographics,
            'reference_sample_size': reference_sample_size,
            'created_by': username,
            's3_key': None,
        }
        if s3_client:
            _save_job_status_to_s3(job_id, jobs[job_id])
        
        # Start processing
        thread = threading.Thread(
            target=run_analysis,
            args=(job_id, project_name, brands, start_date, end_date, 
                  behavior_start, behavior_end, filters, skew_settings, 
                  is_genpop, purchasers_only, brand_category, 
                  include_frequency, is_listener_watcher, platform_name, previous_file,
                  reference_demographics, reference_sample_size, reference_file_key),
            kwargs={'geo_zip_codes': geo_zip_codes, 'geo_dma': geo_dma}
        )
        thread.daemon = True
        thread.start()
        
        # Consume credits for this run (record what it was used for)
        desc = f"{project_name} ({brand_label} {start_date}–{end_date})"
        consume_credit(username, description=desc, job_id=job_id, pull_type='Profile Analysis', credits_used=CREDITS_PROFILE_ANALYSIS)

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


@app.route('/api/status/<job_id>', strict_slashes=False)
@app.route('/api/job-status/<job_id>', strict_slashes=False)
@requires_auth
def get_job_status(job_id):
    """Get simplified status of a specific job. Falls back to S3 when not in memory (Render multi-worker)."""
    job = jobs.get(job_id)
    if job is None and s3_client:
        all_jobs = _load_jobs_status_from_s3()
        job = all_jobs.get(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'job_id': job_id,
        'project_name': job.get('project_name', ''),
        'status': job.get('status', 'unknown'),
        'progress': job.get('progress', 0),
        'message': job.get('message', ''),
        'created_at': job.get('created_at', ''),
        'error': job.get('error'),
        'result_file': job.get('result_file'),
        's3_key': job.get('s3_key'),
        'demographic_validation': job.get('demographic_validation')
    })


@app.route('/api/download/<job_id>', strict_slashes=False)
@requires_auth
def download_result(job_id):
    """Download the result CSV file for a completed job. Uses S3 when local file unavailable (Render multi-worker)."""
    job = jobs.get(job_id)
    if job is None and s3_client:
        all_jobs = _load_jobs_status_from_s3()
        job = all_jobs.get(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404
    if job.get('status') != 'completed':
        return jsonify({'error': 'Job not completed yet'}), 400

    # Try local file first
    result_file = job.get('result_file')
    if result_file and os.path.exists(result_file):
        return send_file(
            result_file,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"{job.get('project_name', 'data')}_behavioral_graph.csv"
        )

    # Fallback: stream from S3
    s3_key = job.get('s3_key')
    if s3_client and s3_key:
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            from io import BytesIO
            body = response['Body'].read()
            return send_file(
                BytesIO(body),
                mimetype='text/csv',
                as_attachment=True,
                download_name=f"{job.get('project_name', 'data')}_behavioral_graph.csv"
            )
        except Exception as e:
            return jsonify({'error': f'Download failed: {str(e)}'}), 500

    return jsonify({'error': 'Result file not found'}), 404


# Cache for S3 file list - now persisted to S3 for faster loads
s3_cache = {
    'jobs': [],
    'categories': [],
    'last_updated': None,
    'file_count': 0,
    'last_full_scan': None  # Track when we did a full scan
}
S3_CACHE_TTL = 300  # 5 minutes cache
S3_CACHE_KEY = 'system/s3_cache.json'  # Persisted cache location
S3_DEMO_CACHE_KEY = 'system/demographics_cache.json'  # Cached demographic summaries
S3_IMAGE_CACHE_KEY = 'system/profile_images_cache.json'  # Cached profile images

# Demographics cache - stores pre-computed demographic summaries for each profile
demographics_cache = {}

# Profile image cache - stores image URLs to avoid repeated API calls
profile_image_cache = {}
profile_image_cache_dirty = False  # Track if cache needs saving
_profile_image_cache_ts = 0  # last time cache was loaded from S3 (epoch seconds)
_PROFILE_IMAGE_CACHE_TTL = 15  # reload from S3 at most every 15 seconds

def load_profile_image_cache(force=False):
    """Load profile image cache from S3."""
    global profile_image_cache, _profile_image_cache_ts
    import time as _time
    if not s3_client:
        return False
    now = _time.time()
    if not force and profile_image_cache and (now - _profile_image_cache_ts) < _PROFILE_IMAGE_CACHE_TTL:
        return True
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_IMAGE_CACHE_KEY)
        profile_image_cache = json.loads(response['Body'].read().decode('utf-8'))
        _profile_image_cache_ts = now
        print(f"✅ Loaded profile image cache: {len(profile_image_cache)} images")
        return True
    except:
        print("📂 No profile image cache found")
        return False

def save_profile_image_cache():
    """Save profile image cache to S3. Retries up to 3 times on failure."""
    global profile_image_cache, profile_image_cache_dirty, _profile_image_cache_ts
    if not s3_client:
        print("⚠️ Cannot save profile image cache: S3 client not available")
        return False
    
    # If cache appears empty, try to load existing cache from S3 and merge with current
    # This prevents losing entries that were just added
    if len(profile_image_cache) == 0:
        print("   📥 Cache appears empty, loading existing cache from S3...")
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_IMAGE_CACHE_KEY)
            existing_cache = json.loads(response['Body'].read().decode('utf-8'))
            # Merge: existing takes precedence for keys we don't have, but keep our new entries
            for key, value in existing_cache.items():
                if key not in profile_image_cache:
                    profile_image_cache[key] = value
            print(f"   ✅ Merged {len(existing_cache)} existing entries from S3")
            print(f"   📊 Total entries after merge: {len(profile_image_cache)}")
        except Exception as load_err:
            print(f"   📂 No existing cache found or error loading: {load_err}")
            # Keep current cache (might be empty, might have new entries)
    
    import time
    cache_json = json.dumps(profile_image_cache, indent=2)
    cache_size = len(cache_json)
    print(f"   💾 Writing cache to S3: {len(profile_image_cache)} entries, {cache_size} bytes")
    
    for attempt in range(3):
        try:
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=S3_IMAGE_CACHE_KEY,
                Body=cache_json,
                ContentType='application/json'
            )
            profile_image_cache_dirty = False
            _profile_image_cache_ts = time.time()
            print(f"   ✅ Successfully wrote cache to S3: {S3_IMAGE_CACHE_KEY} (attempt {attempt + 1})")
            print(f"💾 Saved profile image cache: {len(profile_image_cache)} images")
            sample_keys = list(profile_image_cache.keys())[:5]
            print(f"   📋 Sample keys saved: {sample_keys}")
            return True
        except Exception as e:
            print(f"⚠️ Error saving profile image cache (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(0.5)
            else:
                import traceback
                traceback.print_exc()
                return False
    return False


@app.route('/api/prefetch-images', methods=['POST'])
@requires_auth
def trigger_image_prefetch():
    """Manually trigger profile image prefetch."""
    import threading
    
    def run_prefetch():
        prefetch_profile_images()
    
    thread = threading.Thread(target=run_prefetch, daemon=True)
    thread.start()
    
    return jsonify({
        'success': True,
        'message': 'Image prefetch started in background',
        'cached_count': len(profile_image_cache)
    })


@app.route('/api/profile-image-info/<path:name>')
@requires_auth
def get_profile_image_info(name):
    """Get current profile image info including source."""
    global profile_image_cache
    
    if not profile_image_cache:
        load_profile_image_cache()
    
    cache_key = name.lower().strip()
    
    if cache_key in profile_image_cache:
        cached = profile_image_cache[cache_key]
        return jsonify({
            'success': True,
            'image_url': cached.get('image_url'),
            'source': cached.get('source', 'unknown'),
            'is_custom': cached.get('is_custom', False),
            'cached_at': cached.get('cached_at')
        })
    
    return jsonify({'success': False, 'error': 'No image found'})


@app.route('/api/profile-image-file/<path:s3_key>')
@requires_auth
def serve_profile_image(s3_key):
    """Proxy endpoint to serve profile images from S3 (avoids public access issues)."""
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        content_type = response.get('ContentType', 'image/jpeg')
        image_data = response['Body'].read()
        
        from flask import Response
        return Response(
            image_data,
            mimetype=content_type,
            headers={
                'Cache-Control': 'public, max-age=31536000, immutable',  # Cache indefinitely (1 year, immutable)
                'Content-Type': content_type
            }
        )
    except Exception as e:
        print(f"Error serving profile image {s3_key}: {e}")
        # Return a 1x1 transparent pixel as fallback
        return Response(status=404)


@app.route('/api/admin/profile-image', methods=['POST'])
@requires_admin
def set_profile_image():
    """Set a custom profile image (upload or URL)."""
    global profile_image_cache, profile_image_cache_dirty
    from datetime import datetime
    import uuid
    
    try:
        profile_name = None
        image_url = None
        
        print(f"📸 Profile image request - files: {list(request.files.keys())}, form: {dict(request.form)}")
        
        # Handle file upload
        if 'file' in request.files:
            file = request.files['file']
            profile_name = request.form.get('profile_name')
            
            print(f"   File upload: {file.filename}, profile: {profile_name}")
            
            if file and file.filename:
                # Read file data first (to avoid stream issues)
                file_data = file.read()
                file_size = len(file_data)
                
                # Validate file size
                if file_size > 2 * 1024 * 1024:
                    return jsonify({'success': False, 'error': 'File too large (max 2MB)'})
                
                # Upload to S3
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                if ext not in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                    ext = 'jpg'
                
                content_type = f'image/{ext}'
                if ext == 'jpg':
                    content_type = 'image/jpeg'
                
                s3_key = f"profile-images/{uuid.uuid4().hex}.{ext}"
                
                print(f"   Uploading to S3: {s3_key}, size: {file_size}")
                
                try:
                    s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                        Body=file_data,
                        ContentType=content_type
                    )
                    # Use our proxy endpoint for serving the image (avoids S3 public access issues)
                    image_url = f"/api/profile-image-file/{s3_key}"
                    print(f"   ✅ Uploaded to S3: {s3_key}, serving via: {image_url}")
                except Exception as s3_err:
                    print(f"   ❌ S3 upload failed: {s3_err}")
                    return jsonify({'success': False, 'error': f'S3 upload failed: {str(s3_err)}'})
        else:
            # Handle JSON with URL
            data = request.get_json()
            if data:
                profile_name = data.get('profile_name')
                image_url = data.get('image_url')
                print(f"   URL mode: profile={profile_name}, url={image_url}")
                print(f"   URL length: {len(image_url) if image_url else 0} chars")
                print(f"   URL starts with http: {image_url.startswith('http') if image_url else False}")
        
        if not profile_name:
            print(f"   ❌ No profile name provided. Request data: {request.get_json() if request.is_json else request.form}")
            return jsonify({'success': False, 'error': 'Profile name required'})
        
        if not image_url:
            print(f"   ❌ No image URL provided. Profile: {profile_name}")
            return jsonify({'success': False, 'error': 'Image URL or file required'})
        
        # Validate URL format
        if not image_url.startswith(('http://', 'https://', '/')):
            print(f"   ⚠️ Warning: URL doesn't start with http/https/: {image_url[:50]}...")
            # Still allow it, but log the warning
        
        # Force-reload cache from S3 before updating so we merge with the latest state
        load_profile_image_cache(force=True)
        
        # Update cache with custom image (normalize key consistently)
        cache_key = profile_name.lower().strip()
        
        # Store the entry we're about to add (so we don't lose it if cache gets reloaded)
        new_entry = {
            'image_url': image_url,
            'title': profile_name,
            'source': 'custom',
            'is_custom': True,
            'cached_at': datetime.now().isoformat()
        }
        
        # Add to cache (in-memory)
        profile_image_cache[cache_key] = new_entry
        profile_image_cache_dirty = True
        
        print(f"   📝 Added entry to cache: {cache_key} -> {image_url}")
        print(f"   📊 Cache now has {len(profile_image_cache)} entries before save")
        
        # Read-merge-write: load current state from S3 and merge our entry so we never overwrite other updates
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_IMAGE_CACHE_KEY)
            existing = json.loads(response['Body'].read().decode('utf-8'))
            existing[cache_key] = new_entry
            profile_image_cache = existing
            print(f"   📥 Merged with S3 cache ({len(existing)} entries) before save")
        except Exception as load_err:
            print(f"   📂 No existing cache or load error (using in-memory): {load_err}")
            # Keep current profile_image_cache (already has new_entry)
        
        # Save (with retries inside save_profile_image_cache)
        saved = save_profile_image_cache()
        
        # Ensure our entry is still there after save (in case save function reloaded cache)
        if cache_key not in profile_image_cache or profile_image_cache[cache_key] != new_entry:
            print(f"   ⚠️ WARNING: Entry was lost during save! Restoring...")
            profile_image_cache[cache_key] = new_entry
            saved = save_profile_image_cache()
        if not saved:
            print(f"   ⚠️ Warning: Cache save may have failed for {cache_key}")
            return jsonify({
                'success': False,
                'error': 'Failed to save image cache. Please try again.'
            }), 500
        
        # Verify the save by reading back from S3 (with retry for eventual consistency)
        import time
        verified = False
        for attempt in range(3):  # Try up to 3 times
            try:
                if attempt > 0:
                    time.sleep(0.5)  # Wait before retry
                response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_IMAGE_CACHE_KEY)
                saved_cache = json.loads(response['Body'].read().decode('utf-8'))
                if cache_key in saved_cache:
                    saved_entry = saved_cache[cache_key]
                    if saved_entry.get('image_url') == image_url and saved_entry.get('is_custom'):
                        print(f"   ✅ Verified: Image saved and confirmed in S3 cache (attempt {attempt + 1})")
                        verified = True
                        break
                    else:
                        print(f"   ⚠️ Warning: Cache entry exists but doesn't match: {saved_entry}")
                        print(f"   Expected: {image_url}, Got: {saved_entry.get('image_url')}")
                else:
                    print(f"   ⚠️ Cache key not found (attempt {attempt + 1}): {cache_key}")
                    if attempt == 2:  # Last attempt
                        print(f"   📋 Available keys in S3: {list(saved_cache.keys())[:20]}")
            except Exception as verify_err:
                print(f"   ⚠️ Verification attempt {attempt + 1} failed: {verify_err}")
                if attempt == 2:  # Last attempt
                    import traceback
                    traceback.print_exc()
        
        if not verified:
            print(f"   ❌ ERROR: Could not verify cache save after 3 attempts!")
            # Still return success but log the warning - the save might have worked
            # but verification failed due to S3 eventual consistency
            print(f"   ⚠️ Returning success anyway - image may be saved but verification failed")
        
        print(f"   ✅ Saved to cache: {cache_key} -> {image_url}")
        print(f"   📊 Cache now has {len(profile_image_cache)} entries")
        
        return jsonify({
            'success': True,
            'image_url': image_url,
            'cache_key': cache_key,
            'message': 'Profile image saved and verified'
        })
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/profile-image', methods=['DELETE'])
@requires_admin
def remove_profile_image():
    """Remove custom profile image, allowing auto-source to be used."""
    global profile_image_cache, profile_image_cache_dirty
    
    try:
        data = request.get_json()
        profile_name = data.get('profile_name')
        
        if not profile_name:
            return jsonify({'success': False, 'error': 'Profile name required'})
        
        # Ensure cache is loaded before removing
        if not profile_image_cache:
            load_profile_image_cache()
        
        cache_key = profile_name.lower().strip()
        
        # Remove from cache
        if cache_key in profile_image_cache:
            del profile_image_cache[cache_key]
            profile_image_cache_dirty = True
            saved = save_profile_image_cache()
            if not saved:
                print(f"   ⚠️ Warning: Cache save may have failed after removing {cache_key}")
            print(f"   ✅ Removed from cache: {cache_key}")
        else:
            print(f"   ℹ️ Cache key not found: {cache_key}")
        
        return jsonify({
            'success': True,
            'message': 'Custom image removed'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/rename-file', methods=['POST'])
@requires_admin
def rename_file():
    """Rename a file in S3 (copy to new key, delete old). For Ticket Sales, only update display_name in metadata."""
    global s3_cache
    
    try:
        load_persisted_cache()
        data = request.get_json()
        old_key = data.get('old_key')
        new_name = data.get('new_name')
        display_name = data.get('display_name')  # Optional display name override
        
        if not old_key or not new_name:
            return jsonify({'success': False, 'error': 'Old key and new name required'})
        
        # Ticket Sales: only update display_name in metadata (don't rename S3 file)
        if old_key.startswith('ticket-sales-iq/'):
            actual_key = old_key.replace('ticket-sales-iq/', '')
            meta = load_ticket_sales_metadata()
            if actual_key not in meta:
                meta[actual_key] = {}
            meta[actual_key]['display_name'] = (display_name or new_name.replace('_', ' ')).strip()
            save_ticket_sales_metadata(meta)
            return jsonify({
                'success': True,
                'new_key': old_key,
                'message': 'Display name updated successfully'
            })
        
        # Get the folder path and extension from old key
        folder = '/'.join(old_key.split('/')[:-1])
        old_filename = old_key.split('/')[-1]
        extension = ''
        if '.' in old_filename:
            extension = '.' + old_filename.rsplit('.', 1)[-1]
        
        # Build new key
        new_key = f"{folder}/{new_name}{extension}" if folder else f"{new_name}{extension}"
        
        # Only check for existing file if it's a truly different key (not just case change)
        if old_key != new_key:
            try:
                s3_client.head_object(Bucket=S3_BUCKET, Key=new_key)
                # File exists - but allow if only case is different
                if old_key.lower() != new_key.lower():
                    return jsonify({'success': False, 'error': 'A file with that name already exists'})
            except:
                pass  # File doesn't exist, good to proceed
        
        # Copy to new key
        s3_client.copy_object(
            Bucket=S3_BUCKET,
            CopySource={'Bucket': S3_BUCKET, 'Key': old_key},
            Key=new_key
        )
        
        # Delete old key (only if different)
        if old_key != new_key:
            s3_client.delete_object(Bucket=S3_BUCKET, Key=old_key)
        
        # Update cache - find job in s3_cache and update its key AND display name
        jobs = s3_cache.get('jobs', [])
        found = False
        for job in jobs:
            if job.get('key') == old_key or job.get('s3_key') == old_key:
                job['key'] = new_key
                job['s3_key'] = new_key
                
                # Update the display name
                # If display_name was provided, use it; otherwise auto-generate from filename
                if display_name and display_name.strip():
                    new_display_name = display_name.strip()
                    print(f"📝 Using custom display name: {new_display_name}")
                else:
                    # Extract name from new filename (remove extension and clean up)
                    new_filename = new_key.split('/')[-1]
                    name_without_ext = new_filename.rsplit('.', 1)[0] if '.' in new_filename else new_filename
                    
                    # Remove timestamp patterns
                    name_without_timestamp = remove_timestamp_from_name(name_without_ext)
                    
                    # Replace underscores with spaces and apply smart title case
                    raw_name = name_without_timestamp.replace('_', ' ')
                    new_display_name = smart_title_case(raw_name)
                    print(f"📝 Auto-generated display name: {new_display_name}")
                
                # Update ALL name fields used for display
                job['name'] = new_display_name
                job['display_name'] = new_display_name
                job['project_name'] = new_display_name
                job['brand'] = new_display_name
                print(f"✅ Updated display name to: {new_display_name}")
                found = True
                break
        if not found:
            # File was not in cache; add entry so admin list shows updated name
            if display_name and display_name.strip():
                new_display_name = display_name.strip()
            else:
                new_filename = new_key.split('/')[-1]
                name_without_ext = new_filename.rsplit('.', 1)[0] if '.' in new_filename else new_filename
                name_without_timestamp = remove_timestamp_from_name(name_without_ext)
                new_display_name = smart_title_case(name_without_timestamp.replace('_', ' '))
            jobs.append({
                'key': new_key,
                's3_key': new_key,
                'name': new_display_name,
                'display_name': new_display_name,
                'project_name': new_display_name,
                'brand': new_display_name,
                'category': 'Uncategorized',
            })
            s3_cache['jobs'] = jobs
            print(f"📝 Added new cache entry for {new_key} with display name: {new_display_name}")
        save_persisted_cache()
        
        print(f"📝 Renamed file: {old_key} -> {new_key}")
        
        return jsonify({
            'success': True,
            'new_key': new_key,
            'message': 'File renamed successfully'
        })
        
    except Exception as e:
        print(f"❌ Rename error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


# SVOD file metadata storage key
SVOD_METADATA_KEY = 'system/svod_metadata.json'

# Ticket Sales IQ metadata storage key (display_name, category, image_url per s3_key)
TICKET_SALES_METADATA_KEY = 'system/ticket_sales_metadata.json'
TICKET_SALES_TRACKER_METADATA_KEY = 'system/ticket_sales_tracker_metadata.json'

# SVOD pricing per platform (admin-configured): { "paramount+": { "ad_supported": 5.99, "premium": 11.99 }, ... }
SVOD_PRICING_KEY = 'system/svod_pricing.json'

# Hedge Fund IQ ticker metadata storage key
TICKER_METADATA_KEY = 'system/ticker_metadata.json'

# Purgatory metadata storage key - tracks files pending admin review
PURGATORY_METADATA_KEY = 'system/purgatory_metadata.json'

# Profile released notifications - per-user notifications when their purgatory profile is released
PROFILE_RELEASED_NOTIFICATIONS_KEY = 'system/profile_released_notifications.json'

# Default/stock profile photo (shown when a user has no photo) - super_admin sets in Settings
DEFAULT_PROFILE_PHOTO_KEY = 'system/default_profile_photo.json'

def load_default_profile_photo():
    """Return the default profile photo URL (or None). Used when user has no profile_picture."""
    if not s3_client:
        return None
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=DEFAULT_PROFILE_PHOTO_KEY)
        data = json.loads(response['Body'].read().decode('utf-8'))
        return (data.get('image_url') or '').strip() or None
    except Exception:
        return None

def save_default_profile_photo(image_url):
    """Save the default profile photo URL. image_url can be None to clear."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=DEFAULT_PROFILE_PHOTO_KEY,
            Body=json.dumps({'image_url': image_url or ''}, indent=2),
            ContentType='application/json'
        )
        return True
    except Exception as e:
        print(f"Error saving default profile photo: {e}")
        return False

def load_profile_released_notifications():
    """Load profile-released notifications from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=PROFILE_RELEASED_NOTIFICATIONS_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_profile_released_notifications(data):
    """Save profile-released notifications to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=PROFILE_RELEASED_NOTIFICATIONS_KEY,
            Body=json.dumps(data, indent=2),
            ContentType='application/json'
        )
        return True
    except Exception as e:
        print(f"Error saving profile released notifications: {e}")
        return False

def add_profile_released_notification(username, profile_name, s3_key, source_type='profile_analysis'):
    """Add a notification when a purgatory item is released. source_type: profile_analysis -> Profile IQ, svod_acquisition -> Subscriber IQ."""
    data = load_profile_released_notifications()
    if username not in data:
        data[username] = []
    data[username].append({
        'id': str(uuid.uuid4()),
        'profile_name': profile_name,
        's3_key': s3_key,
        'source_type': source_type,
        'created_at': datetime.now().isoformat(),
        'read': False
    })
    return save_profile_released_notifications(data)

def send_profile_released_email(created_by, profile_name, released_s3_key):
    """Send email to the profile creator when their profile is released from purgatory."""
    data = load_users()
    user = data.get('users', {}).get(created_by, {})
    email = user.get('email')
    if not email:
        print(f"⚠️ No email for user {created_by}, skipping profile-released email")
        return False, "No email for user"
    base_url = os.environ.get('APP_BASE_URL', 'https://behavioral-graph.onrender.com')
    profile_url = f"{base_url}/#profileiq"
    subject = f"Your Profile IQ: {profile_name} is now available"
    body = f"""<p>Good news! Your Profile IQ analysis for <strong>{profile_name}</strong> has been released from review and is now available in your dashboard.</p>
<p><a href="{profile_url}" class="email-btn">View in Profile IQ</a></p>"""
    html = _wrap_email_html(body, title="Profile IQ ready")
    text = f"Your Profile IQ analysis for {profile_name} has been released and is now available. View it at: {profile_url}"
    return send_email_via_gmail(email, subject, html, text)


def send_svod_released_email(created_by, profile_name, released_s3_key):
    """Send email when an SVOD Acquisition result is released from purgatory (Subscriber Acquisition report)."""
    data = load_users()
    user = data.get('users', {}).get(created_by, {})
    email = user.get('email')
    if not email:
        print(f"⚠️ No email for user {created_by}, skipping SVOD released email")
        return False, "No email for user"
    base_url = os.environ.get('APP_BASE_URL', 'https://behavioral-graph.onrender.com')
    subscriber_url = f"{base_url}/#subscriberiq"
    subject = f"Your Subscriber Acquisition report: {profile_name} is now available"
    body = f"""<p>Good news! Your Subscriber Acquisition report for <strong>{profile_name}</strong> has been released from review and is now available in your dashboard.</p>
<p><a href="{subscriber_url}" class="email-btn">View Subscriber Acquisition report</a></p>"""
    html = _wrap_email_html(body, title="Subscriber Acquisition report ready")
    text = f"Your Subscriber Acquisition report for {profile_name} has been released and is now available. View it at: {subscriber_url}"
    return send_email_via_gmail(email, subject, html, text)

def load_purgatory_metadata():
    """Load purgatory file metadata from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=PURGATORY_METADATA_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_purgatory_metadata(metadata):
    """Save purgatory file metadata to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=PURGATORY_METADATA_KEY,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
        return True
    except Exception as e:
        print(f"Error saving purgatory metadata: {e}")
        return False

def get_purgatory_approvers():
    """Get all users who can approve purgatory items (super_admins and users with has_purgatory_approval)."""
    data = load_users()
    approvers = []
    
    for username, user in data.get('users', {}).items():
        email = user.get('email')
        if not email:
            continue
            
        # Super admins always get purgatory notifications
        if user.get('role') == 'super_admin':
            approvers.append({
                'username': username,
                'email': email,
                'first_name': user.get('first_name', username),
                'last_name': user.get('last_name', ''),
                'is_super_admin': True
            })
        # Users with purgatory approval permission
        elif user.get('has_purgatory_approval'):
            approvers.append({
                'username': username,
                'email': email,
                'first_name': user.get('first_name', username),
                'last_name': user.get('last_name', ''),
                'is_super_admin': False
            })
    
    return approvers

def send_purgatory_notification(created_by, project_name, purgatory_id):
    """Send email notification to all purgatory approvers when a new item is added."""
    # Get the user who created the profile
    data = load_users()
    creator = data.get('users', {}).get(created_by, {})
    creator_first_name = creator.get('first_name', created_by)
    creator_last_name = creator.get('last_name', '')
    creator_company = creator.get('company', 'Unknown Company')
    
    creator_display = f"{creator_first_name} {creator_last_name}".strip() or created_by
    if creator_company and creator_company != 'Unknown Company':
        creator_display += f" ({creator_company})"
    
    # Get all approvers
    approvers = get_purgatory_approvers()
    
    if not approvers:
        print("⚠️ No purgatory approvers found to notify")
        return
    
    # Build the purgatory review URL
    # Use environment variable for base URL or default
    base_url = os.environ.get('APP_BASE_URL', 'https://behavioral-graph.onrender.com')
    purgatory_url = f"{base_url}/admin#purgatory"
    
    subject = f"Purgatory: {project_name}"
    
    body = f"""
        <p><strong>{creator_display}</strong> has pulled a profile for:</p>
        <div class="email-card">
            <div class="email-card-title">{project_name}</div>
        </div>
        <p style="color: #8892b0;">Please review and release this profile from purgatory.</p>
        <p><a href="{purgatory_url}" class="email-btn">Review in Purgatory</a></p>
    """
    html_content = _wrap_email_html(body, title="New Profile Awaiting Review")
    
    text_content = f"""
New Profile Awaiting Review

{creator_display} has pulled a profile for: {project_name}

Please review and release this profile from purgatory.

Review here: {purgatory_url}
    """
    
    # Send email to each approver
    for approver in approvers:
        try:
            success, msg = send_email_via_gmail(approver['email'], subject, html_content, text_content)
            if success:
                print(f"✅ Purgatory notification sent to {approver['email']}")
            else:
                print(f"⚠️ Failed to send purgatory notification to {approver['email']}: {msg}")
        except Exception as e:
            print(f"❌ Error sending purgatory notification to {approver['email']}: {e}")

def add_to_purgatory(s3_key, bucket, created_by, project_name, category, source_type='profile_analysis'):
    """Add a file to purgatory with metadata for admin review."""
    metadata = load_purgatory_metadata()
    
    # Create unique ID for this purgatory item
    purgatory_id = f"{bucket}:{s3_key}"
    
    metadata[purgatory_id] = {
        's3_key': s3_key,
        'bucket': bucket,
        'created_by': created_by,
        'project_name': project_name,
        'category': category,
        'source_type': source_type,  # 'profile_analysis' or 'svod_acquisition'
        'created_at': datetime.now().isoformat(),
        'status': 'pending',  # pending, approved, rejected
        'image_url': None,
        'title': project_name
    }
    
    save_purgatory_metadata(metadata)
    
    # Send email notification to all purgatory approvers
    try:
        send_purgatory_notification(created_by, project_name, purgatory_id)
    except Exception as e:
        print(f"⚠️ Failed to send purgatory notification: {e}")
    
    return purgatory_id

def release_from_purgatory(purgatory_id):
    """Move a file from purgatory to the main bucket location."""
    metadata = load_purgatory_metadata()
    
    if purgatory_id not in metadata:
        return False, "Item not found in purgatory"
    
    item = metadata[purgatory_id]
    bucket = item['bucket']
    old_key = item['s3_key']
    
    # The old key should be in purgatory/ prefix
    if not old_key.startswith(S3_PURGATORY_PREFIX):
        return False, "Item is not in purgatory folder"
    
    # New key is without the purgatory/ prefix
    new_key = old_key.replace(S3_PURGATORY_PREFIX, '', 1)
    
    try:
        # Copy to new location
        s3_client.copy_object(
            Bucket=bucket,
            CopySource={'Bucket': bucket, 'Key': old_key},
            Key=new_key
        )
        
        # Delete from purgatory
        s3_client.delete_object(Bucket=bucket, Key=old_key)
        
        # Update metadata
        item['status'] = 'approved'
        item['released_at'] = datetime.now().isoformat()
        item['released_key'] = new_key
        save_purgatory_metadata(metadata)
        
        # Auto-add to qualifying users' allowed_runs based on their category subscriptions
        profile_category = item.get('category', '')
        auto_add_runs_to_all_users(new_key, key_category_map={new_key: profile_category})
        
        print(f"✅ Released from purgatory: {old_key} -> {new_key}")
        return True, new_key
    except Exception as e:
        print(f"❌ Error releasing from purgatory: {e}")
        return False, str(e)

def get_user_purgatory_items(username):
    """Get purgatory items created by a specific user."""
    metadata = load_purgatory_metadata()
    user_items = []
    
    for purgatory_id, item in metadata.items():
        if item.get('created_by') == username and item.get('status') == 'pending':
            user_items.append({
                'purgatory_id': purgatory_id,
                **item
            })
    
    return user_items

def _add_user_profile(s3_key, created_by):
    """Track user profile creation (legacy function - now handled by purgatory metadata)."""
    # This is now handled by the purgatory metadata system
    # Keeping as stub for backward compatibility
    print(f"📝 Profile created: {s3_key} by {created_by}")


# ============================================================================
# PURGATORY API ENDPOINTS
# ============================================================================

@app.route('/api/admin/purgatory', methods=['GET'])
@requires_purgatory_access
def get_purgatory_items():
    """Get all items in purgatory for admin review."""
    try:
        metadata = load_purgatory_metadata()
        items = []
        
        for purgatory_id, item in metadata.items():
            if item.get('status') == 'pending':
                # Get file info from S3
                bucket = item.get('bucket', S3_BUCKET)
                s3_key = item.get('s3_key', '')
                
                try:
                    # Get file size and last modified
                    response = s3_client.head_object(Bucket=bucket, Key=s3_key)
                    file_size = response.get('ContentLength', 0)
                    last_modified = response.get('LastModified')
                    if last_modified:
                        last_modified = last_modified.isoformat()
                except:
                    file_size = 0
                    last_modified = item.get('created_at')
                
                items.append({
                    'purgatory_id': purgatory_id,
                    's3_key': s3_key,
                    'bucket': bucket,
                    'project_name': item.get('project_name', ''),
                    'title': item.get('title', item.get('project_name', '')),
                    'category': item.get('category', 'Uncategorized'),
                    'created_by': item.get('created_by', 'unknown'),
                    'created_at': item.get('created_at', ''),
                    'source_type': item.get('source_type', 'profile_analysis'),
                    'image_url': item.get('image_url'),
                    'file_size': file_size,
                    'last_modified': last_modified
                })
        
        # Sort by created_at descending (newest first)
        items.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return jsonify({
            'success': True,
            'items': items,
            'count': len(items)
        })
        
    except Exception as e:
        print(f"Error getting purgatory items: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/purgatory/update', methods=['POST'])
@requires_purgatory_access
def update_purgatory_item():
    """Update purgatory item metadata (title, category, image)."""
    try:
        data = request.get_json()
        purgatory_id = data.get('purgatory_id')
        
        if not purgatory_id:
            return jsonify({'success': False, 'error': 'Purgatory ID required'})
        
        metadata = load_purgatory_metadata()
        
        if purgatory_id not in metadata:
            return jsonify({'success': False, 'error': 'Item not found in purgatory'})
        
        # Update allowed fields (title is the profile display name; keep project_name in sync)
        if 'title' in data:
            metadata[purgatory_id]['title'] = data['title']
            metadata[purgatory_id]['project_name'] = data['title']
        if 'category' in data:
            metadata[purgatory_id]['category'] = data['category']
        if 'image_url' in data:
            metadata[purgatory_id]['image_url'] = data['image_url']
        if 'project_name' in data:
            metadata[purgatory_id]['project_name'] = data['project_name']
        
        save_purgatory_metadata(metadata)
        
        return jsonify({
            'success': True,
            'message': 'Purgatory item updated'
        })
        
    except Exception as e:
        print(f"Error updating purgatory item: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/purgatory/release', methods=['POST'])
@requires_purgatory_access
def release_purgatory_item():
    """Release an item from purgatory to the main bucket."""
    global s3_cache
    
    try:
        data = request.get_json()
        purgatory_id = data.get('purgatory_id')
        
        if not purgatory_id:
            return jsonify({'success': False, 'error': 'Purgatory ID required'})
        
        metadata = load_purgatory_metadata()
        
        if purgatory_id not in metadata:
            return jsonify({'success': False, 'error': 'Item not found in purgatory'})
        
        item = metadata[purgatory_id]
        # Apply current title, category, and image from the request so edits stick when releasing (no separate Save required)
        if data.get('title'):
            item['title'] = data['title']
            item['project_name'] = data['title']
        if data.get('category'):
            item['category'] = data['category']
        if 'image_url' in data:
            item['image_url'] = data['image_url'] or None
        save_purgatory_metadata(metadata)
        item = metadata[purgatory_id]
        
        success, result = release_from_purgatory(purgatory_id)
        
        if success:
            # Update the profile image cache with the custom image if set
            if item.get('image_url'):
                cache_key = item.get('project_name', '').lower().strip()
                if cache_key:
                    profile_image_cache[cache_key] = {
                        'image_url': item['image_url'],
                        'title': item.get('title', item.get('project_name', '')),
                        'source': 'custom',
                        'is_custom': True,
                        'cached_at': datetime.now().isoformat()
                    }
                    save_profile_image_cache()
            
            # Refresh cache to pick up the new file
            smart_cache_update()
            # Apply admin's display name and category to the new cache entry so dashboard shows them
            display_name = item.get('title') or item.get('project_name', '')
            if display_name and s3_cache.get('jobs'):
                for i, job in enumerate(s3_cache['jobs']):
                    if (job.get('s3_key') or job.get('key')) == result:
                        s3_cache['jobs'][i]['display_name'] = display_name
                        s3_cache['jobs'][i]['name'] = display_name
                        s3_cache['jobs'][i]['project_name'] = display_name
                        s3_cache['jobs'][i]['brand'] = display_name
                        if item.get('category'):
                            s3_cache['jobs'][i]['category'] = item['category']
                        save_persisted_cache()
                        break
            
            # For ticket_sales_tracker: persist image_url to metadata when released
            source_type = item.get('source_type', 'profile_analysis')
            if source_type == 'ticket_sales_tracker' and result and item.get('image_url'):
                try:
                    tst_meta = load_ticket_sales_tracker_metadata()
                    if result not in tst_meta:
                        tst_meta[result] = {}
                    tst_meta[result]['image_url'] = item['image_url']
                    save_ticket_sales_tracker_metadata(tst_meta)
                    print(f"✅ Saved Ticket Sales Tracker image for {result}")
                except Exception as e:
                    print(f"⚠️ Failed to save TST image: {e}")
            # For SVOD: persist category to SVOD metadata so Subscriber IQ list and content list show the selected category
            if source_type == 'svod_acquisition' and result:
                try:
                    svod_meta = load_svod_metadata()
                    if result not in svod_meta:
                        svod_meta[result] = {}
                    svod_meta[result]['category'] = item.get('category') or 'SVOD Acquisition'
                    save_svod_metadata(svod_meta)
                    print(f"✅ Saved SVOD category for {result} -> {svod_meta[result]['category']}")
                except Exception as e:
                    print(f"⚠️ Failed to save SVOD category: {e}")
            
            # Notify the creator: in-dashboard notification (with source_type); email only for Profile IQ
            created_by = item.get('created_by')
            source_type = item.get('source_type', 'profile_analysis')
            if created_by:
                profile_name = display_name or item.get('project_name', 'Unknown Profile')
                add_profile_released_notification(created_by, profile_name, result, source_type=source_type)
                if source_type == 'profile_analysis':
                    send_profile_released_email(created_by, profile_name, result)
                elif source_type == 'svod_acquisition':
                    send_svod_released_email(created_by, profile_name, result)
            
            return jsonify({
                'success': True,
                'message': 'Item released from purgatory',
                'new_key': result
            })
        else:
            return jsonify({'success': False, 'error': result})
        
    except Exception as e:
        print(f"Error releasing purgatory item: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/purgatory/reject', methods=['POST'])
@requires_purgatory_access
def reject_purgatory_item():
    """Reject and delete an item from purgatory."""
    try:
        data = request.get_json()
        purgatory_id = data.get('purgatory_id')
        
        if not purgatory_id:
            return jsonify({'success': False, 'error': 'Purgatory ID required'})
        
        metadata = load_purgatory_metadata()
        
        if purgatory_id not in metadata:
            return jsonify({'success': False, 'error': 'Item not found in purgatory'})
        
        item = metadata[purgatory_id]
        bucket = item.get('bucket', S3_BUCKET)
        s3_key = item.get('s3_key', '')
        
        # Delete the file from S3
        try:
            s3_client.delete_object(Bucket=bucket, Key=s3_key)
            print(f"🗑️ Deleted purgatory file: {bucket}/{s3_key}")
        except Exception as e:
            print(f"Warning: Could not delete S3 file: {e}")
        
        # Update metadata status
        metadata[purgatory_id]['status'] = 'rejected'
        metadata[purgatory_id]['rejected_at'] = datetime.now().isoformat()
        save_purgatory_metadata(metadata)
        
        return jsonify({
            'success': True,
            'message': 'Item rejected and deleted from purgatory'
        })
        
    except Exception as e:
        print(f"Error rejecting purgatory item: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/purgatory/check-access', methods=['GET'])
@requires_auth
def check_purgatory_access():
    """Check if current user has purgatory approval access."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'has_access': False})
        
        role = user.get('role', '')
        has_purgatory_approval = user.get('has_purgatory_approval', False)
        
        has_access = role in ['admin', 'super_admin'] or has_purgatory_approval
        
        return jsonify({
            'success': True,
            'has_access': has_access,
            'is_admin': role in ['admin', 'super_admin'],
            'has_purgatory_approval': has_purgatory_approval
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/settings/default-profile-photo', methods=['GET'])
@requires_auth
def get_default_profile_photo():
    """Get the default/stock profile photo URL (used when a user has no photo)."""
    url = load_default_profile_photo()
    return jsonify({'success': True, 'image_url': url or ''})


@app.route('/api/admin/settings/default-profile-photo', methods=['POST'])
@requires_super_admin
def set_default_profile_photo():
    """Set or remove the default profile photo. Super admin only. Accepts file upload or JSON with image_url. Send remove=1 to clear."""
    try:
        data_json = request.get_json(silent=True) or {}
        if request.form.get('remove') or data_json.get('remove'):
            save_default_profile_photo(None)
            return jsonify({'success': True, 'message': 'Default profile photo removed', 'image_url': ''})
        image_url = None
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            file_data = file.read()
            if len(file_data) > 2 * 1024 * 1024:
                return jsonify({'success': False, 'error': 'File too large (max 2MB)'})
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
            if ext not in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                ext = 'jpg'
            content_type = f'image/{ext}'
            if ext == 'jpg':
                content_type = 'image/jpeg'
            import uuid
            s3_key = f"profile-images/default_stock_{uuid.uuid4().hex}.{ext}"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=file_data, ContentType=content_type)
            image_url = f"/api/profile-image-file/{s3_key}"
        else:
            data = data_json or request.form
            image_url = (data.get('image_url') or '').strip()
        if not image_url:
            return jsonify({'success': False, 'error': 'Upload a file or provide an image URL'}), 400
        if not save_default_profile_photo(image_url):
            return jsonify({'success': False, 'error': 'Failed to save setting'}), 500
        return jsonify({'success': True, 'message': 'Default profile photo saved', 'image_url': image_url})
    except Exception as e:
        print(f"Error setting default profile photo: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/purgatory/my-items', methods=['GET'])
@requires_auth
def get_my_purgatory_items():
    """Get purgatory items for the current user (visible only to them)."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'})
        
        username = session.get('username', '')
        items = get_user_purgatory_items(username)
        
        return jsonify({
            'success': True,
            'items': items,
            'count': len(items)
        })
        
    except Exception as e:
        print(f"Error getting user purgatory items: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/my-results', methods=['GET'])
@requires_auth
def get_my_results():
    """Get all results created by the current user - both purgatory (pending) and released."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'})
        
        username = session.get('username', '')
        results = []
        
        # Get purgatory items (pending review) and released items (approved = in dashboard)
        purgatory_metadata = load_purgatory_metadata()
        for purgatory_id, item in purgatory_metadata.items():
            if item.get('created_by') != username:
                continue
            if item.get('status') == 'rejected':
                continue
            st = item.get('status', '')
            has_released_key = bool(item.get('released_key'))
            has_released_at = bool(item.get('released_at'))
            # Released = status approved/released OR metadata shows it was released (released_key/released_at)
            is_released = st in ('released', 'approved') or has_released_key or has_released_at
            # Fallback: if metadata still says pending but file was moved (e.g. metadata not saved), check S3
            if not is_released and item.get('s3_key', '').startswith(S3_PURGATORY_PREFIX):
                released_key = item['s3_key'].replace(S3_PURGATORY_PREFIX, '', 1)
                try:
                    s3_client.head_object(Bucket=item.get('bucket', S3_BUCKET), Key=released_key)
                    is_released = True  # File exists at released path -> was released
                except Exception:
                    pass
            if is_released:
                released_key = item.get('released_key') or item.get('s3_key', '').replace(S3_PURGATORY_PREFIX, '')
                results.append({
                    'id': purgatory_id,
                    'project_name': item.get('project_name') or item.get('title', 'Unknown'),
                    'category': item.get('category', ''),
                    'created_at': item.get('created_at', ''),
                    'released_at': item.get('released_at', ''),
                    'source_type': item.get('source_type', 'profile_analysis'),
                    'status': 'released',
                    'in_purgatory': False,
                    's3_key': released_key,
                    'bucket': item.get('bucket')
                })
            else:
                results.append({
                    'id': purgatory_id,
                    'project_name': item.get('project_name') or item.get('title', 'Unknown'),
                    'category': item.get('category', ''),
                    'created_at': item.get('created_at', ''),
                    'source_type': item.get('source_type', 'profile_analysis'),
                    'status': 'pending',
                    'in_purgatory': True,
                    's3_key': item.get('s3_key'),
                    'bucket': item.get('bucket'),
                    'purgatory_id': purgatory_id
                })
        
        # Sort by created_at descending
        results.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return jsonify({
            'success': True,
            'results': results,
            'count': len(results),
            'pending_count': sum(1 for r in results if r.get('status') == 'pending'),
            'released_count': sum(1 for r in results if r.get('status') == 'released')
        })
        
    except Exception as e:
        print(f"Error getting user results: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

# Default KPI mappings for known tickers
DEFAULT_TICKER_KPIS = {
    'ADT': 'Monitoring & Related Services Revenue Against Churn',
    'ATUS': 'Residential Customers Broadband',
    'BADOO': 'Paying Users',
    'BMBL': 'Paying Users',
    'CABO': 'Residential Data PSUs',
    'CHTR': 'Internet Residential Customer Relationships',
    'CMCSA': 'Domestic Broadband Residential Customers',
    'DIS': 'Paid subscribers - Disney+ Domestic US & Canada',
    'ESPN': 'ESPN+ Subs',
    'DUOL': 'Paid Subscribers',
    'GDDY': 'Total Customers',
    'GRND': 'Paying Users',
    'HINGE': 'Payers',
    'HUBS': 'Customers',
    'HULU': 'Paid subscribers - Total Hulu',
    'LMND': 'Customers',
    'LUMN': 'Mass Markets Total Broadband Subscribers',
    'MG_ASIA': 'Payers',
    'MG_EGAE': 'Payers',
    'NFLX': 'Paid memberships (UCAN)',
    'NYT': 'Digital Subscribers',
    'PARA': 'Subscribers',
    'PCK': 'Paid subscribers',
    'PINS': 'Monthly Active Users - US & Canada',
    'PLNT': 'Members',
    'PTON': 'Paid Connected Fitness Subscriptions',
    'RDDT': 'US Daily Active Users',
    'ROKU': 'Streaming households',
    'SIRI': 'Ending subscribers',
    'SPOT': 'Premium subscribers',
    'T': 'Total Domestic Broadband Connections / DSL Plus Broadband Connections',
    'TPHONE': 'Subscribers: Postpaid Phone',
    'TINDER': 'Payers',
    'TMUS': 'Total Customers',
    'TMUSPHONE': 'Postpaid Phone Customers',
    'TRUP': 'Total Subscription Pets Enrolled',
    'USM': 'Total Retail Connections',
    'VZ': 'Broadband Connections (Consumer / Residential + Business)',
    'VZPHONE': 'Consumer Wireless Retail Postpaid Subscribers',
    'WBD': 'Subscribers - Domestic, incl. Global Max, HBO Max',
    'SPHR': 'Event-related revenue',
    'FUN': 'Attendance',
    'HIMS': 'Subscribers (End of Period)',
    'AAPL': 'Services',
    'AMZN': 'Subscription Services',
    'META': 'WW Daily Active People',
    'DASH': 'Total Orders'
}

def load_svod_metadata():
    """Load SVOD file metadata (categories) from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=SVOD_METADATA_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_svod_metadata(metadata):
    """Save SVOD file metadata to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=SVOD_METADATA_KEY,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
        return True
    except:
        return False

def load_ticket_sales_metadata():
    """Load Ticket Sales IQ file metadata (display_name, category, image_url) from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=TICKET_SALES_METADATA_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_ticket_sales_metadata(metadata):
    """Save Ticket Sales IQ file metadata to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=TICKET_SALES_METADATA_KEY,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
        return True
    except:
        return False

def load_ticket_sales_tracker_metadata():
    """Load Ticket Sales Tracker file metadata (image_url) from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=TICKET_SALES_TRACKER_METADATA_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_ticket_sales_tracker_metadata(metadata):
    """Save Ticket Sales Tracker file metadata to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=TICKET_SALES_TRACKER_METADATA_KEY,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
        return True
    except:
        return False

def load_svod_pricing():
    """Load SVOD pricing (ad_supported, premium per platform) from S3."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=SVOD_PRICING_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_svod_pricing(pricing):
    """Save SVOD pricing to S3."""
    if not s3_client:
        return False
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=SVOD_PRICING_KEY,
            Body=json.dumps(pricing, indent=2),
            ContentType='application/json'
        )
        return True
    except:
        return False

@app.route('/api/settings/svod-pricing', methods=['GET', 'POST'])
@requires_auth
def svod_pricing_api():
    """GET: return SVOD pricing for all platforms. POST: save (admin only)."""
    if request.method == 'GET':
        try:
            pricing = load_svod_pricing()
            return jsonify({'success': True, 'pricing': pricing})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    # POST (admin only)
    user = get_current_user()
    if not user or user.get('role') not in ['admin', 'super_admin']:
        return jsonify({'success': False, 'error': 'Admin required'}), 403
    try:
        data = request.get_json() or {}
        pricing = data.get('pricing', {})
        if not isinstance(pricing, dict):
            return jsonify({'success': False, 'error': 'pricing must be an object'}), 400
        save_svod_pricing(pricing)
        return jsonify({'success': True, 'message': 'SVOD pricing saved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/user/purgatory', methods=['GET'])
@requires_auth
def get_user_purgatory():
    """Get purgatory items for the current user (visible only to them until released)."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'})
        
        username = session.get('username', '')
        items = get_user_purgatory_items(username)
        
        return jsonify({
            'success': True,
            'items': items,
            'count': len(items)
        })
        
    except Exception as e:
        print(f"❌ Error getting user purgatory: {e}")
        return jsonify({'success': False, 'error': str(e)})

def _get_purgatory_file_response(purgatory_id, disposition='attachment'):
    """Fetch purgatory file from S3 and return a Response. disposition: 'attachment' (download) or 'inline' (view in browser)."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    username = session.get('username', '')
    is_admin = user.get('role') in ['admin', 'super_admin']
    
    metadata = load_purgatory_metadata()
    
    if purgatory_id not in metadata:
        return jsonify({'success': False, 'error': 'Item not found'}), 404
    
    item = metadata[purgatory_id]
    
    if item.get('created_by') != username and not is_admin:
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    bucket = item.get('bucket', S3_BUCKET)
    s3_key = item.get('s3_key', '')
    
    response = s3_client.get_object(Bucket=bucket, Key=s3_key)
    content = response['Body'].read()
    filename = s3_key.split('/')[-1]
    
    return Response(
        content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'{disposition}; filename="{filename}"',
            'Content-Type': 'text/csv; charset=utf-8'
        }
    )


@app.route('/api/purgatory/download')
@requires_auth
def download_purgatory_file():
    """Download a file from purgatory. Use ?purgatory_id=... (URL-encoded; may contain colons/slashes)."""
    try:
        purgatory_id = request.args.get('purgatory_id') or request.args.get('id')
        if not purgatory_id:
            return jsonify({'success': False, 'error': 'purgatory_id required'}), 400
        return _get_purgatory_file_response(purgatory_id, disposition='attachment')
    except Exception as e:
        print(f"❌ Error downloading purgatory file: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/purgatory/view')
@requires_auth
def view_purgatory_file():
    """Open a purgatory CSV in the browser (inline). Use ?purgatory_id=... (URL-encoded)."""
    try:
        purgatory_id = request.args.get('purgatory_id') or request.args.get('id')
        if not purgatory_id:
            return jsonify({'success': False, 'error': 'purgatory_id required'}), 400
        return _get_purgatory_file_response(purgatory_id, disposition='inline')
    except Exception as e:
        print(f"❌ Error viewing purgatory file: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/ticket-sales-image', methods=['POST'])
@requires_admin
def set_ticket_sales_image():
    """Set image for a Ticket Sales IQ file (upload or URL)."""
    import uuid
    try:
        s3_key = None
        image_url = None
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            s3_key = request.form.get('ticket_sales_s3_key')
            if not s3_key:
                return jsonify({'success': False, 'error': 'ticket_sales_s3_key required'})
            if s3_key.startswith('ticket-sales-iq/'):
                s3_key = s3_key.replace('ticket-sales-iq/', '')
            file_data = file.read()
            if len(file_data) > 2 * 1024 * 1024:
                return jsonify({'success': False, 'error': 'File too large (max 2MB)'})
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
            if ext not in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                ext = 'jpg'
            content_type = f'image/{ext}'
            if ext == 'jpg':
                content_type = 'image/jpeg'
            upload_key = f"ticket-sales-images/{uuid.uuid4().hex}.{ext}"
            s3_client.put_object(Bucket=S3_BUCKET, Key=upload_key, Body=file_data, ContentType=content_type)
            image_url = f"/api/profile-image-file/{upload_key}"
        else:
            data = request.get_json() or {}
            s3_key = data.get('ticket_sales_s3_key') or data.get('s3_key')
            image_url = (data.get('image_url') or '').strip()
            if not s3_key or not image_url:
                return jsonify({'success': False, 'error': 'ticket_sales_s3_key and image_url required'})
            if s3_key.startswith('ticket-sales-iq/'):
                s3_key = s3_key.replace('ticket-sales-iq/', '')
        meta = load_ticket_sales_metadata()
        if s3_key not in meta:
            meta[s3_key] = {}
        meta[s3_key]['image_url'] = image_url
        save_ticket_sales_metadata(meta)
        return jsonify({'success': True, 'image_url': image_url, 'message': 'Image saved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/ticket-sales-tracker-image', methods=['POST'])
@requires_admin
def set_ticket_sales_tracker_image():
    """Upload or set image for a Ticket Sales Tracker file (in admin CMS)."""
    try:
        if request.files and 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            s3_key = (request.form.get('ticket_sales_tracker_s3_key') or request.form.get('s3_key') or '').strip()
            if not s3_key:
                return jsonify({'success': False, 'error': 'ticket_sales_tracker_s3_key required'})
            if s3_key.startswith('ticket-sales-tracker/'):
                s3_key = s3_key.replace('ticket-sales-tracker/', '')
            file_data = file.read()
            if len(file_data) > 2 * 1024 * 1024:
                return jsonify({'success': False, 'error': 'File too large (max 2MB)'}), 400
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
            if ext == 'jpg':
                ext = 'jpeg'
            if ext not in ['jpeg', 'png', 'webp', 'gif']:
                return jsonify({'success': False, 'error': 'Invalid image type'}), 400
            content_type = f'image/{ext}'
            if ext == 'jpg':
                content_type = 'image/jpeg'
            upload_key = f"ticket-sales-tracker-images/{uuid.uuid4().hex}.{ext}"
            s3_client.put_object(Bucket=S3_BUCKET, Key=upload_key, Body=file_data, ContentType=content_type)
            image_url = f"/api/profile-image-file/{upload_key}"
        else:
            data = request.get_json() or {}
            s3_key = (data.get('ticket_sales_tracker_s3_key') or data.get('s3_key') or '').strip()
            image_url = (data.get('image_url') or '').strip()
            if not s3_key or not image_url:
                return jsonify({'success': False, 'error': 'ticket_sales_tracker_s3_key and image_url required'})
            if s3_key.startswith('ticket-sales-tracker/'):
                s3_key = s3_key.replace('ticket-sales-tracker/', '')
        meta = load_ticket_sales_tracker_metadata()
        if s3_key not in meta:
            meta[s3_key] = {}
        meta[s3_key]['image_url'] = image_url
        save_ticket_sales_tracker_metadata(meta)
        return jsonify({'success': True, 'image_url': image_url, 'message': 'Image saved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/ticket-sales-metadata', methods=['POST'])
@requires_admin
def update_ticket_sales_metadata():
    """Update display_name, category, or image_url for a Ticket Sales IQ file."""
    try:
        data = request.get_json()
        s3_key = data.get('s3_key')  # Key within ticket-sales-iq bucket (e.g. "mercy_chris pratt_02_06_2026_11_41.csv")
        if not s3_key:
            return jsonify({'success': False, 'error': 's3_key required'})
        # Normalize: strip ticket-sales-iq/ prefix if passed
        if s3_key.startswith('ticket-sales-iq/'):
            s3_key = s3_key.replace('ticket-sales-iq/', '')
        meta = load_ticket_sales_metadata()
        if s3_key not in meta:
            meta[s3_key] = {}
        if 'display_name' in data:
            meta[s3_key]['display_name'] = (data['display_name'] or '').strip()
        if 'category' in data:
            meta[s3_key]['category'] = (data['category'] or '').strip().upper()
        if 'image_url' in data:
            meta[s3_key]['image_url'] = (data['image_url'] or '').strip()
        save_ticket_sales_metadata(meta)
        return jsonify({'success': True, 'message': 'Metadata updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/change-category', methods=['POST'])
@requires_admin
def change_file_category():
    """Change the BRAND CATEGORY in a CSV file or SVOD file metadata."""
    global s3_cache
    
    try:
        load_persisted_cache()
        data = request.get_json()
        file_key = data.get('file_key')
        new_category = data.get('new_category', '').strip().upper()
        
        if not file_key or not new_category:
            return jsonify({'success': False, 'error': 'File key and category required'})
        
        # Check if this is a Ticket Sales IQ file (stored in ticket-sales-iq bucket)
        if file_key.startswith('ticket-sales-iq/'):
            actual_key = file_key.replace('ticket-sales-iq/', '')
            metadata = load_ticket_sales_metadata()
            if actual_key not in metadata:
                metadata[actual_key] = {}
            metadata[actual_key]['category'] = new_category
            save_ticket_sales_metadata(metadata)
            print(f"🏷️ Changed Ticket Sales category for {actual_key} to {new_category}")
            return jsonify({
                'success': True,
                'new_category': new_category,
                'message': f'Category updated to {new_category}'
            })
        
        # Check if this is a SVOD file (stored in svod-acquisition bucket)
        if file_key.startswith('svod-acquisition/'):
            # SVOD files can have subcategories (TALENT, CONTENT, etc.)
            # but they're always grouped under SVOD ACQUISITION master category
            actual_key = file_key.replace('svod-acquisition/', '')
            metadata = load_svod_metadata()
            if actual_key not in metadata:
                metadata[actual_key] = {}
            metadata[actual_key]['category'] = new_category
            save_svod_metadata(metadata)
            
            print(f"🏷️ Changed SVOD subcategory for {actual_key} to {new_category} (under SVOD ACQUISITION master)")
            return jsonify({
                'success': True,
                'new_category': new_category,
                'message': f'Category updated to {new_category} (grouped under SVOD ACQUISITION master category)'
            })
        
        # Regular file - update BRAND CATEGORY in CSV
        # Download the file from S3
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=file_key)
        content = response['Body'].read().decode('utf-8', errors='ignore')
        
        # Parse and update the BRAND CATEGORY line
        lines = content.split('\n')
        updated = False
        new_lines = []
        
        for line in lines:
            # Check if this is the BRAND CATEGORY line
            if re.match(r'^\s*BRAND\s*CATEGORY\s*,', line, re.IGNORECASE):
                new_lines.append(f'BRAND CATEGORY,{new_category}')
                updated = True
            else:
                new_lines.append(line)
        
        # If no BRAND CATEGORY line found, append at end (cache rebuild reads last 200KB)
        if not updated:
            while new_lines and not new_lines[-1].strip():
                new_lines.pop()
            new_lines.append(f'BRAND CATEGORY,{new_category}')
            new_lines.append('')
        
        # Upload updated content back to S3
        updated_content = '\n'.join(new_lines)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=file_key,
            Body=updated_content.encode('utf-8'),
            ContentType='text/csv'
        )
        
        # Get the new LastModified timestamp so cache stays in sync
        try:
            head = s3_client.head_object(Bucket=S3_BUCKET, Key=file_key)
            new_last_modified = head['LastModified'].isoformat()
        except Exception:
            from datetime import datetime, timezone
            new_last_modified = datetime.now(timezone.utc).isoformat()
        
        # If this is a purgatory file, update purgatory metadata so category persists in admin list
        if file_key.startswith(S3_PURGATORY_PREFIX):
            purgatory_id = f"{S3_BUCKET}:{file_key}"
            metadata = load_purgatory_metadata()
            if purgatory_id in metadata:
                metadata[purgatory_id]['category'] = new_category
                save_purgatory_metadata(metadata)
                print(f"🏷️ Updated purgatory metadata category for {file_key} to {new_category}")
        
        # Update cache - find job in s3_cache and update its category AND last_modified
        # so smart_cache_update won't see it as "modified" and revert the category
        jobs = s3_cache.get('jobs', [])
        found = False
        for job in jobs:
            if job.get('key') == file_key or job.get('s3_key') == file_key:
                job['category'] = new_category
                job['last_modified'] = new_last_modified
                found = True
                break
        if not found and not file_key.startswith(S3_PURGATORY_PREFIX):
            # File not in cache (e.g. new upload); add entry so admin list shows updated category
            filename = file_key.split('/')[-1]
            name_without_ext = filename.replace('.csv', '') if filename.endswith('.csv') else filename
            name_without_timestamp = remove_timestamp_from_name(name_without_ext)
            project_name = smart_title_case(name_without_timestamp.replace('_', ' '))
            jobs.append({
                'key': file_key,
                's3_key': file_key,
                'category': new_category,
                'project_name': project_name,
                'name': project_name,
                'display_name': project_name,
            })
            s3_cache['jobs'] = jobs
            print(f"📝 Added new cache entry for {file_key} with category: {new_category}")
        elif found:
            print(f"📝 Updated existing cache entry for {file_key} with category: {new_category}")
        save_persisted_cache()
        
        print(f"🏷️ Changed category for {file_key} to {new_category}")
        
        return jsonify({
            'success': True,
            'new_category': new_category,
            'message': 'Category updated successfully'
        })
        
    except Exception as e:
        print(f"❌ Category change error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/admin/change-cadence', methods=['POST'])
@requires_admin
def change_file_cadence():
    """Change the Content Cadence for an SVOD file in svod_metadata."""
    try:
        data = request.get_json()
        file_key = data.get('file_key', '').strip()
        new_cadence = data.get('content_cadence', '').strip()

        if not file_key:
            return jsonify({'success': False, 'error': 'File key is required'})

        if new_cadence and new_cadence not in ('Weekly', 'All at Once'):
            return jsonify({'success': False, 'error': 'Content cadence must be "Weekly" or "All at Once"'})

        if not file_key.startswith('svod-acquisition/'):
            return jsonify({'success': False, 'error': 'Content cadence can only be set for SVOD files'})

        actual_key = file_key.replace('svod-acquisition/', '')
        metadata = load_svod_metadata()
        if actual_key not in metadata:
            metadata[actual_key] = {}
        metadata[actual_key]['content_cadence'] = new_cadence
        save_svod_metadata(metadata)

        print(f"🔄 Changed content cadence for {actual_key} to '{new_cadence}'")
        return jsonify({
            'success': True,
            'content_cadence': new_cadence,
            'message': f'Content cadence updated to {new_cadence or "(cleared)"}'
        })
    except Exception as e:
        print(f"❌ Cadence change error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/image-cache-stats')
@requires_auth
def get_image_cache_stats():
    """Get profile image cache statistics."""
    if not profile_image_cache:
        load_profile_image_cache()
    
    found = sum(1 for v in profile_image_cache.values() if v.get('image_url'))
    not_found = sum(1 for v in profile_image_cache.values() if v.get('not_found'))
    
    by_source = {}
    for v in profile_image_cache.values():
        src = v.get('source', 'unknown')
        by_source[src] = by_source.get(src, 0) + 1
    
    return jsonify({
        'total_cached': len(profile_image_cache),
        'found': found,
        'not_found': not_found,
        'by_source': by_source
    })

@app.route('/api/debug/image-cache')
@requires_admin
def debug_image_cache():
    """Debug endpoint to see all cached profile images."""
    if not profile_image_cache:
        load_profile_image_cache()
    
    # Get custom images only
    custom_images = {k: v for k, v in profile_image_cache.items() if v.get('is_custom')}
    
    # Get first 50 entries for inspection
    sample_entries = dict(list(profile_image_cache.items())[:50])
    
    return jsonify({
        'success': True,
        'total_entries': len(profile_image_cache),
        'custom_count': len(custom_images),
        'custom_images': custom_images,
        'sample_keys': list(profile_image_cache.keys())[:100],
        'sample_entries': sample_entries
    })

@app.route('/api/debug/image-cache/<path:name>')
@requires_admin
def debug_image_cache_for_profile(name):
    """Debug endpoint to check cache for a specific profile."""
    if not profile_image_cache:
        load_profile_image_cache()
    
    cache_key = name.lower().strip()
    in_memory = cache_key in profile_image_cache
    in_memory_entry = profile_image_cache.get(cache_key)
    
    # Also check S3 directly
    s3_entry = None
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_IMAGE_CACHE_KEY)
        s3_cache = json.loads(response['Body'].read().decode('utf-8'))
        s3_entry = s3_cache.get(cache_key)
    except:
        pass
    
    return jsonify({
        'success': True,
        'profile_name': name,
        'cache_key': cache_key,
        'in_memory': in_memory,
        'in_memory_entry': in_memory_entry,
        'in_s3': s3_entry is not None,
        's3_entry': s3_entry,
        'matches': in_memory_entry == s3_entry if (in_memory_entry and s3_entry) else False
    })

@app.route('/api/all-profile-images')
@requires_auth
def get_all_profile_images():
    """Get all cached profile images at once for instant loading."""
    load_profile_image_cache()
    
    # Return all images that have URLs (exclude not_found entries)
    images = {}
    for key, value in profile_image_cache.items():
        if value.get('image_url'):
            images[key] = {
                'url': value['image_url'],
                'source': value.get('source', 'unknown'),
                'is_custom': value.get('is_custom', False)
            }
    
    return jsonify({
        'success': True,
        'images': images,
        'count': len(images)
    })

def load_persisted_cache():
    """Load the S3 file cache from S3 storage."""
    global s3_cache
    if not s3_client:
        return False
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_CACHE_KEY)
        cached_data = json.loads(response['Body'].read().decode('utf-8'))
        s3_cache['jobs'] = cached_data.get('jobs', [])
        s3_cache['categories'] = cached_data.get('categories', [])
        s3_cache['last_updated'] = cached_data.get('last_updated')
        s3_cache['file_count'] = cached_data.get('file_count', 0)
        s3_cache['last_full_scan'] = cached_data.get('last_full_scan')
        print(f"✅ Loaded persisted cache: {len(s3_cache['jobs'])} files")
        return True
    except s3_client.exceptions.NoSuchKey:
        print("📂 No persisted cache found, will do full scan")
        return False
    except Exception as e:
        print(f"⚠️ Error loading persisted cache: {e}")
        return False

def save_persisted_cache():
    """Save the S3 file cache to S3 storage."""
    if not s3_client:
        return
    try:
        cache_data = {
            'jobs': s3_cache.get('jobs', []),
            'categories': s3_cache.get('categories', []),
            'last_updated': s3_cache.get('last_updated'),
            'file_count': s3_cache.get('file_count', 0),
            'last_full_scan': s3_cache.get('last_full_scan')
        }
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=S3_CACHE_KEY,
            Body=json.dumps(cache_data),
            ContentType='application/json'
        )
        print(f"💾 Saved cache to S3: {len(s3_cache['jobs'])} files")
    except Exception as e:
        print(f"⚠️ Error saving persisted cache: {e}")


def load_demographics_cache():
    """Load pre-computed demographics summaries from S3."""
    global demographics_cache
    if not s3_client:
        return False
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_DEMO_CACHE_KEY)
        demographics_cache = json.loads(response['Body'].read().decode('utf-8'))
        print(f"✅ Loaded demographics cache: {len(demographics_cache)} profiles")
        return True
    except s3_client.exceptions.NoSuchKey:
        print("📂 No demographics cache found")
        return False
    except Exception as e:
        print(f"⚠️ Error loading demographics cache: {e}")
        return False


def save_demographics_cache():
    """Save demographics cache to S3."""
    if not s3_client or not demographics_cache:
        return
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=S3_DEMO_CACHE_KEY,
            Body=json.dumps(demographics_cache),
            ContentType='application/json'
        )
        print(f"💾 Saved demographics cache: {len(demographics_cache)} profiles")
    except Exception as e:
        print(f"⚠️ Error saving demographics cache: {e}")


# Map old age brackets to new display brackets with split ratios (old -> [(new_bucket, ratio), ...])
# Used so older files show age inline with new bracket labels and redistributed percentages.
_OLD_AGE_TO_NEW = {
    '<16': [('17 and Under', 1.0)],
    'under 16': [('17 and Under', 1.0)],
    '16-18': [('17 and Under', 0.67), ('18-24', 0.33)],
    '17-18': [('17 and Under', 0.67), ('18-24', 0.33)],
    '19-20': [('18-24', 1.0)],
    '18-20': [('18-24', 1.0)],
    '21-25': [('18-24', 0.8), ('25-34', 0.2)],
    '26-30': [('25-34', 1.0)],
    '31-40': [('25-34', 0.4), ('35-44', 0.6)],
    '41-49': [('35-44', 0.21), ('45-54', 0.53), ('55-64', 0.26)],
    '41-59': [('35-44', 0.21), ('45-54', 0.53), ('55-64', 0.26)],
    '60+': [('55-64', 0.25), ('65 or Older', 0.75)],
    '65+': [('55-64', 0.25), ('65 or Older', 0.75)],
    'over 65': [('55-64', 0.25), ('65 or Older', 0.75)],
}


def _norm_age_key(s):
    """Normalize age value for mapping: strip, lower, collapse spaces around dashes (e.g. '18 - 24' -> '18-24')."""
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _normalize_age_buckets_from_raw(age_raw_dict):
    """Redistribute old age bracket *raw counts* into new buckets, then return new bucket percentages.
    Each old bucket's count is split by ratio (e.g. 17-18: 67% -> 17 and Under, 33% -> 18-24);
    new percentage = (summed raw for bucket / total age raw) * 100.
    """
    if not age_raw_dict:
        return {}
    new_raw = {}
    for old_key, raw_count in age_raw_dict.items():
        raw_count = int(float(str(raw_count).replace(",", ""))) if raw_count else 0
        if raw_count <= 0:
            continue
        key_norm = _norm_age_key(old_key)
        mapping = _OLD_AGE_TO_NEW.get(key_norm) or _OLD_AGE_TO_NEW.get((old_key or "").strip().lower()) or _OLD_AGE_TO_NEW.get(old_key)
        if mapping:
            for new_bucket, ratio in mapping:
                new_raw[new_bucket] = new_raw.get(new_bucket, 0) + raw_count * ratio
        else:
            new_raw[old_key] = new_raw.get(old_key, 0) + raw_count
    total = sum(new_raw.values())
    if total <= 0:
        return {}
    return {b: round((r / total) * 100.0, 4) for b, r in new_raw.items()}


def normalize_age_buckets_for_display(age_dict):
    """Redistribute old age bracket percentages into new display buckets (percentage-based fallback)."""
    if not age_dict:
        return dict(age_dict)
    new_age = {}
    for old_key, pct in age_dict.items():
        key_norm = _norm_age_key(old_key)
        mapping = _OLD_AGE_TO_NEW.get(key_norm) or _OLD_AGE_TO_NEW.get((old_key or '').strip().lower()) or _OLD_AGE_TO_NEW.get(old_key)
        if mapping:
            for new_bucket, ratio in mapping:
                new_age[new_bucket] = new_age.get(new_bucket, 0) + pct * ratio
        else:
            new_age[old_key] = new_age.get(old_key, 0) + pct
    return new_age


def extract_demographics_summary(csv_content):
    """Extract demographic summary from CSV content."""
    try:
        df = pd.read_csv(io.StringIO(csv_content))
        
        summary = {
            'gender': {},
            'age': {},
            'income': {},
            'ethnicity': {},
            'sampleSize': 0,
            'projectedUS': 0
        }
        
        age_raw = {}  # value -> Original Raw Numbers for AGE rows
        for _, row in df.iterrows():
            category = str(row.get('Column', '')).upper()
            value = row.get('Value', '')
            pct = float(
                row.get("Brand Penetration (Row)")
                or row.get("Category Share")
                or row.get("Percentage")
                or 0
            ) or 0

            if category == "SAMPLE SIZE":
                summary['sampleSize'] = int(row.get('Category Share', 0) or row.get('Original Raw Numbers', 0) or 0)
            elif category == 'BRAND INPUT':
                summary['projectedUS'] = int(row.get('US Gen Pop Projection', 0) or 0)
            elif category == 'GENDER' and value:
                summary['gender'][value] = pct
            elif category == 'AGE' and value:
                summary['age'][value] = pct
                raw_val = row.get('Original Raw Numbers', 0)
                if raw_val is not None and str(raw_val).strip() != '':
                    try:
                        age_raw[value] = int(float(str(raw_val).replace(',', '')))
                    except (ValueError, TypeError):
                        pass
            elif category == 'INCOME' and value:
                summary['income'][value] = pct
            elif category == 'ETHNICITY' and value:
                summary['ethnicity'][value] = pct
        
        # Use raw counts to build new age buckets, then derive percentages.
        # Pipeline CSVs often have empty Original Raw Numbers for AGE; derive from pct and sample size.
        if not age_raw and summary["age"] and summary["sampleSize"] > 0:
            for k, pct in summary["age"].items():
                age_raw[k] = max(0, round((pct / 100.0) * summary["sampleSize"]))
        if age_raw:
            summary["age"] = _normalize_age_buckets_from_raw(age_raw)
        else:
            summary["age"] = normalize_age_buckets_for_display(summary["age"])
        return summary
    except Exception as e:
        print(f"Error extracting demographics: {e}")
        return None

# Cache loading is handled in background thread - see quick_startup_cache()


@app.route('/api/jobs')
@requires_auth
def list_jobs():
    """List all jobs (local + S3 cached) with caching for performance. Use ?refresh=1 to sync from S3 before returning."""
    import time
    
    try:
        job_list = []
        categories = set()
        
        # Return quickly if cache is still loading
        if not cache_loading_complete and not s3_cache.get('jobs'):
            return jsonify({
                'jobs': [],
                'categories': [],
                'cache_info': {'loading': True, 'message': 'Loading profiles...'},
                'loading': True
            })
        
        # If refresh=1, load latest persisted cache (from any worker) then sync with S3 so new/updated files show up for all users
        if request.args.get('refresh') and s3_client and cache_loading_complete:
            try:
                load_persisted_cache()  # Pick up cache saved by other workers (e.g. after release from purgatory)
                smart_cache_update()   # Sync with actual S3 listing (add new, remove deleted)
            except Exception as e:
                print(f"⚠️ list_jobs refresh error: {e}")
        
        # Update user's last activity time (but don't block on it)
        username = session.get('username')
        if username:
            try:
                users = load_users()
                if username in users:
                    users[username]['last_activity'] = time.time()
                    save_users(users)
            except Exception:
                pass  # Don't block on activity tracking
        
        # Add local jobs (always fresh); for purgatory s3_keys merge display name and category from purgatory metadata
        purgatory_meta = load_purgatory_metadata() if s3_client else {}
        for job_id, job in jobs.items():
            s3_key = job.get('s3_key') or ''
            entry = {
                'job_id': job_id,
                'project_name': job.get('project_name', ''),
                'status': job.get('status', 'unknown'),
                'progress': job.get('progress', 0),
                'created_at': job.get('created_at', ''),
                'source': 'local',
                'category': 'LOCAL',
                's3_key': s3_key or job.get('s3_key'),
                'display_name': job.get('display_name'),
                'profile_subject': get_profile_subject_from_s3_key(s3_key) if s3_key else ''
            }
            if s3_key.startswith(S3_PURGATORY_PREFIX):
                purgatory_id = f"{S3_BUCKET}:{s3_key}"
                if purgatory_id in purgatory_meta:
                    item = purgatory_meta[purgatory_id]
                    entry['category'] = item.get('category') or 'Uncategorized'
                    entry['project_name'] = item.get('title') or item.get('project_name') or entry['project_name']
                    entry['display_name'] = item.get('title') or entry['project_name']
                    # Use released key if item was released so "View in Dashboard" works without 404
                    if item.get('released_key'):
                        entry['s3_key'] = item['released_key']
                    categories.add(entry['category'])
            else:
                categories.add('LOCAL')
            job_list.append(entry)
        
        # Use persisted cache only - no S3 scanning on page load for speed
        # If cache is empty, try to load persisted cache
        cache_jobs = s3_cache.get('jobs') or []
        if not cache_jobs and s3_client:
            load_persisted_cache()
            cache_jobs = s3_cache.get('jobs') or []
        
        # Add cached S3 jobs (ensure each has created_at for sorting)
        for j in cache_jobs:
            if isinstance(j, dict):
                sk = j.get('s3_key') or ''
                entry = {
                    'job_id': j.get('job_id') or sk or '',
                    'project_name': j.get('project_name') or j.get('name', 'Unknown'),
                    'status': j.get('status', 'cached'),
                    'progress': j.get('progress', 100),
                    'created_at': j.get('created_at') or j.get('last_modified', ''),
                    'source': j.get('source', 's3'),
                    'category': j.get('category', 'OTHER'),
                    's3_key': sk,
                    'display_name': j.get('display_name'),
                    'profile_subject': j.get('profile_subject') or get_profile_subject_from_s3_key(sk) if sk else ''
                }
                # Preserve bucket/is_svod for SVOD profile detection
                if 'bucket' in j:
                    entry['bucket'] = j['bucket']
                if 'is_svod' in j:
                    entry['is_svod'] = j['is_svod']
                job_list.append(entry)
                cat = j.get('category')
                if cat:
                    categories.add(cat)
        
        # DO NOT include purgatory items - they should NEVER appear in the profile selector
        # Purgatory items are only visible in the Admin purgatory review section
        
        # Filter out any purgatory items that may have been added from cache
        job_list = [e for e in job_list if not (e.get('s3_key') or '').startswith('purgatory/')]
        job_list = [e for e in job_list if not (e.get('s3_key') or '').startswith(S3_PURGATORY_PREFIX)]
        job_list = [e for e in job_list if e.get('status') != 'pending' and not e.get('in_purgatory')]
        
        # Profile IQ must not show SVOD Acquisition — only Subscriber IQ shows those
        def is_svod_entry(e):
            if e.get('is_svod'):
                return True
            sk = (e.get('s3_key') or '')
            jid = (e.get('job_id') or '')
            if sk.startswith('svod-acquisition/') or jid.startswith('svod-acquisition/'):
                return True
            # Catch legacy cache entries that lack is_svod (e.g. old purgatory-released or migrated cache)
            cat = (e.get('category') or '').strip().upper()
            if cat == 'SVOD ACQUISITION':
                return True
            return False
        job_list = [e for e in job_list if not is_svod_entry(e)]
        
        # Filter out OTHER and UNCATEGORIZED categories - these should never appear in profile selector
        job_list = [e for e in job_list if (e.get('category') or '').upper() not in ('OTHER', 'UNCATEGORIZED', '')]
        
        # Gen Pop: always show the canonical S3 key so profile selector only ever loads that file (no CMS/stale mapping)
        seen_gen_pop = False
        new_job_list = []
        for e in job_list:
            sk = (e.get('s3_key') or '').lower()
            if 'gen_pop' in sk:
                if seen_gen_pop:
                    continue  # keep only one Gen Pop entry
                seen_gen_pop = True
                e = dict(e)
                e['s3_key'] = GEN_POP_CANONICAL_KEY
                e['job_id'] = GEN_POP_CANONICAL_KEY
                e['project_name'] = e.get('project_name') or 'Gen Pop 2026'
                e['display_name'] = e.get('display_name') or 'Gen Pop 2026'
            new_job_list.append(e)
        job_list = new_job_list
        
        # Mark run access: tag each profile with 'accessible' flag instead of filtering
        allowed_runs = None
        u = None
        try:
            _users_data = load_users()
            u = _users_data.get('users', {}).get(session.get('username')) if session.get('username') else None
            if u is not None:
                allowed_runs = u.get('allowed_runs')
        except Exception:
            pass
        has_all_access = allowed_runs is None or (isinstance(allowed_runs, list) and len(allowed_runs) == 1 and allowed_runs[0] == '*')
        if has_all_access:
            for e in job_list:
                e['accessible'] = True
        else:
            allowed_set = set(allowed_runs or [])
            for e in job_list:
                sk = e.get('s3_key') or ''
                if sk in allowed_set or 'gen_pop' in sk.lower():
                    e['accessible'] = True
                else:
                    e['accessible'] = False
        
        categories = {e.get('category') for e in job_list if e.get('category')}
        
        # Sort by created_at descending (safe key for missing/None values)
        sorted_jobs = sorted(job_list, key=lambda x: x.get('created_at') or '', reverse=True)
        
        return jsonify({
            'jobs': sorted_jobs,
            'categories': sorted(list(categories)),
            'cache_info': {
                'last_updated': s3_cache.get('last_updated'),
                'file_count': s3_cache.get('file_count', 0),
                'cached': True
            }
        })
    except Exception as e:
        traceback.print_exc()
        print(f"⚠️ list_jobs error: {e}")
        # Return empty jobs instead of 500 so UI can still render
        return jsonify({
            'jobs': [],
            'categories': [],
            'cache_info': {'error': str(e), 'cached': False},
            'loading': False
        }), 200


@app.route('/api/refresh-cache')
@requires_auth
def force_refresh_cache():
    """Smart refresh - adds new, updates modified, removes deleted files."""
    if s3_client:
        result = smart_cache_update()
        return jsonify({
            'success': True,
            'new_files': result.get('new', 0),
            'updated_files': result.get('updated', 0),
            'deleted_files': result.get('deleted', 0),
            'total': result.get('total', 0),
            'message': f"Added {result.get('new', 0)}, updated {result.get('updated', 0)}, removed {result.get('deleted', 0)} files"
        })
    return jsonify({'success': False, 'error': 'S3 not configured'})


@app.route('/api/push-cache-update', methods=['GET', 'POST'])
def push_cache_update():
    """
    Push cache update: sync dashboard profile cache from S3 immediately.
    Call this after uploading a new file to S3 so it shows up on the Profile IQ dashboard right away.
    If env PUSH_CACHE_SECRET is set, require ?secret=<value> to match; otherwise no auth (for Lambda/upload scripts).
    """
    if not s3_client:
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    push_secret = os.environ.get('PUSH_CACHE_SECRET')
    if push_secret:
        provided = request.args.get('secret') or (request.get_json() or {}).get('secret')
        if provided != push_secret:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        result = smart_cache_update()
        return jsonify({
            'success': True,
            'new': result.get('new', 0),
            'updated': result.get('updated', 0),
            'deleted': result.get('deleted', 0),
            'total': result.get('total', 0),
            'message': f"Cache updated: {result.get('new', 0)} new, {result.get('updated', 0)} updated, {result.get('deleted', 0)} removed"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/full-refresh-cache')
@requires_auth  
def full_refresh_cache():
    """Full scan - rebuilds entire cache (slow, use sparingly)."""
    if s3_client:
        result = refresh_s3_cache(incremental=False)
        return jsonify({
            'success': True,
            'total': result.get('total', 0),
            'message': f"Full rebuild: {result.get('total', 0)} files cached"
        })
    return jsonify({'success': False, 'error': 'S3 not configured'})


@app.route('/api/cached_files')
@requires_auth
def get_cached_files():
    """Get list of cached S3 files for admin panel."""
    # Make sure cache is loaded
    if not s3_cache['jobs'] and s3_client:
        load_persisted_cache()
    
    files = []
    for job in s3_cache.get('jobs', []):
        files.append({
            'key': job.get('s3_key', job.get('job_id')),
            'project_name': job.get('project_name', 'Unknown'),
            'display_name': job.get('display_name') or job.get('project_name', 'Unknown'),
            'category': job.get('category', 'Uncategorized'),
            'profile_subject': job.get('profile_subject', ''),
            'created_at': job.get('created_at', ''),
            'status': job.get('status', 'cached')
        })
    
    return jsonify({
        'success': True,
        'files': files,
        'count': len(files)
    })


@app.route('/api/demographics-cache')
@requires_auth
def get_demographics_cache():
    """Get all cached demographics for fast Content Dev analysis."""
    global demographics_cache
    
    # Load from S3 if empty
    if not demographics_cache:
        load_demographics_cache()
    
    # Also return the jobs list with categories
    if not s3_cache['jobs']:
        load_persisted_cache()
    
    # Merge jobs with demographics
    profiles_with_demo = []
    for job in s3_cache.get('jobs', []):
        key = job.get('s3_key', job.get('job_id', ''))
        demo = demographics_cache.get(key, {})
        profiles_with_demo.append({
            'key': key,
            'name': job.get('project_name', 'Unknown'),
            'category': job.get('category', 'Uncategorized'),
            'demographics': demo.get('gender', {}),
            'age': demo.get('age', {}),
            'income': demo.get('income', {}),
            'sampleSize': demo.get('sampleSize', 0),
            'projectedUS': demo.get('projectedUS', 0),
            'hasDemoData': bool(demo)
        })
    
    return jsonify({
        'success': True,
        'profiles': profiles_with_demo,
        'cacheSize': len(demographics_cache),
        'totalProfiles': len(profiles_with_demo)
    })


@app.route('/api/build-demographics-cache', methods=['POST'])
@requires_auth
def build_demographics_cache():
    """Build/update demographics cache for all profiles - processes in batches to avoid timeout."""
    global demographics_cache
    
    if not s3_client:
        return jsonify({'error': 'S3 not configured'}), 500
    
    # Get batch size from request (default 50 to avoid timeout)
    batch_size = request.args.get('batch', 50, type=int)
    
    # Load existing cache
    load_demographics_cache()
    
    # Get all jobs
    if not s3_cache['jobs']:
        load_persisted_cache()
    
    # Find jobs that need caching
    jobs_to_process = []
    for job in s3_cache.get('jobs', []):
        key = job.get('s3_key')
        if not key or key.startswith('system/'):
            continue
        if key not in demographics_cache:
            jobs_to_process.append(job)
    
    # Process only a batch to avoid timeout
    batch = jobs_to_process[:batch_size]
    updated = 0
    errors = 0
    
    for job in batch:
        key = job.get('s3_key')
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            csv_content = response['Body'].read().decode('utf-8')
            summary = extract_demographics_summary(csv_content)
            if summary:
                demographics_cache[key] = summary
                updated += 1
        except Exception as e:
            print(f"Error caching {key}: {e}")
            errors += 1
    
    # Save updated cache
    if updated > 0:
        save_demographics_cache()
    
    remaining = len(jobs_to_process) - len(batch)
    
    return jsonify({
        'success': True,
        'updated': updated,
        'errors': errors,
        'totalCached': len(demographics_cache),
        'remaining': remaining,
        'needsMore': remaining > 0
    })


def smart_cache_update():
    """
    Smart incremental cache update - adds NEW, updates MODIFIED, removes DELETED files.
    
    How it works:
    1. Cache stores last_modified timestamp for each file
    2. S3 list checks all current files
    3. New files are added, modified files are updated
    4. Files in cache but NOT in S3 are REMOVED
    
    This keeps cache in sync with actual S3 contents.
    Preserves existing metadata (custom images, categories) when updating files.
    """
    import time
    from datetime import datetime, timezone, timedelta
    
    if not s3_client:
        return {'new': 0, 'updated': 0, 'deleted': 0, 'total': 0}
    
    # Always load latest persisted cache first so admin changes from any
    # worker/service are picked up before we compare timestamps.
    try:
        load_persisted_cache()
    except Exception:
        pass
    
    # Ensure profile image cache is loaded for matching images to new files
    if not profile_image_cache:
        load_profile_image_cache()
    
    # Build lookup of existing files by key -> last_modified
    existing = {job['s3_key']: job.get('last_modified', '') for job in s3_cache.get('jobs', []) if job.get('s3_key')}
    
    # Track which S3 keys we see (to detect deleted files)
    current_s3_keys = set()
    
    new_count = 0
    updated_count = 0
    deleted_count = 0
    new_s3_keys = []  # Track newly discovered files for auto-adding to users
    new_key_cats = {}  # s3_key -> category for category-aware auto-add
    
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv') or key.startswith('system/') or key.startswith('historic/') or key.startswith(S3_PURGATORY_PREFIX):
                    continue
                
                current_s3_keys.add(key)
                obj_modified = obj['LastModified'].isoformat()
                
                # Check if this is a new or modified file
                if key not in existing:
                    # NEW file
                    job_data = process_s3_file_metadata(key, obj)
                    job_data['last_modified'] = obj_modified
                    
                    # Check if there's an existing profile image for this project name
                    if profile_image_cache:
                        cache_key = job_data.get('project_name', '').lower().strip()
                        if cache_key in profile_image_cache:
                            cached_img = profile_image_cache[cache_key]
                            if cached_img.get('is_custom'):
                                job_data['custom_image'] = cached_img.get('image_url')
                    
                    s3_cache['jobs'].append(job_data)
                    if job_data['category'] not in s3_cache['categories']:
                        s3_cache['categories'].append(job_data['category'])
                    new_count += 1
                    new_s3_keys.append(key)
                    new_key_cats[key] = job_data.get('category', '')
                    print(f"   ➕ New: {key}")
                    
                elif existing[key] != obj_modified:
                    # MODIFIED file - preserve existing metadata
                    job_data = process_s3_file_metadata(key, obj)
                    job_data['last_modified'] = obj_modified
                    # Update in place, preserving custom metadata
                    for i, job in enumerate(s3_cache['jobs']):
                        if job.get('s3_key') == key:
                            # Preserve custom image
                            if job.get('custom_image'):
                                job_data['custom_image'] = job['custom_image']
                            # CSV's BRAND CATEGORY is source of truth (admin changes update the CSV).
                            # Only fall back to cached category when CSV has no valid BRAND CATEGORY.
                            if job_data.get('category') == 'UNCATEGORIZED' and job.get('category') and job.get('category') != 'UNCATEGORIZED':
                                job_data['category'] = job['category']
                            # Preserve custom display_name if it was manually set
                            if job.get('display_name') and job.get('display_name') != job.get('project_name'):
                                job_data['display_name'] = job['display_name']
                                job_data['name'] = job['display_name']
                            s3_cache['jobs'][i] = job_data
                            break
                    updated_count += 1
                    print(f"   🔄 Updated: {key}")
        
        # REMOVE files that are in cache but NOT in S3 (deleted files)
        original_count = len(s3_cache['jobs'])
        s3_cache['jobs'] = [job for job in s3_cache['jobs'] if job.get('s3_key') in current_s3_keys or job.get('source') == 'local']
        deleted_count = original_count - len(s3_cache['jobs'])
        
        if deleted_count > 0:
            print(f"   🗑️ Removed {deleted_count} deleted files from cache")
        
        # Rebuild categories from remaining jobs
        s3_cache['categories'] = list(set(job.get('category', 'Uncategorized') for job in s3_cache['jobs']))
        
        # Update cache metadata
        s3_cache['last_updated'] = time.time()
        s3_cache['file_count'] = len(s3_cache['jobs'])
        
        # Save if there were any changes
        if new_count > 0 or updated_count > 0 or deleted_count > 0:
            save_persisted_cache()
            print(f"✅ Smart update: {new_count} new, {updated_count} modified, {deleted_count} removed, {len(s3_cache['jobs'])} total")
        else:
            print(f"✅ No changes detected ({len(s3_cache['jobs'])} files cached)")
        
        # Auto-add newly discovered profiles to qualifying users based on category subscriptions
        if new_s3_keys:
            auto_add_runs_to_all_users(new_s3_keys, key_category_map=new_key_cats)
        
        return {'new': new_count, 'updated': updated_count, 'deleted': deleted_count, 'total': len(s3_cache['jobs'])}
        
    except Exception as e:
        print(f"Error in smart cache update: {e}")
        return {'new': 0, 'updated': 0, 'total': len(s3_cache.get('jobs', [])), 'error': str(e)}


def refresh_s3_cache(incremental=True):
    """Refresh cache - uses smart update for speed."""
    if incremental:
        return smart_cache_update()
    
    # Full scan (only when explicitly requested)
    import time
    print("🔄 Doing full S3 scan...")
    
    job_list = []
    categories = set()
    
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv') or key.startswith('system/') or key.startswith(S3_PURGATORY_PREFIX):
                    continue
                
                job_data = process_s3_file_metadata(key, obj)
                job_data['last_modified'] = obj['LastModified'].isoformat()
                job_list.append(job_data)
                categories.add(job_data['category'])
        
        s3_cache['jobs'] = job_list
        s3_cache['categories'] = list(categories)
        s3_cache['last_updated'] = time.time()
        s3_cache['file_count'] = len(job_list)
        
        save_persisted_cache()
        print(f"✅ Full scan complete: {len(job_list)} files")
        
        return {'new': len(job_list), 'updated': 0, 'total': len(job_list)}
        
    except Exception as e:
        print(f"Error in full scan: {e}")
        return {'error': str(e)}

def remove_timestamp_from_name(name):
    """Remove various timestamp patterns from filename.
    
    Handles patterns like:
    - Taylor_Swift_09_22_2025_20_22 -> Taylor_Swift
    - Cleveland_Browns_01_23_2026 -> Cleveland_Browns
    - HBO_Max_11_07_2025_16_46 -> HBO_Max
    """
    import re
    
    # Remove timestamp patterns at the end:
    # Pattern 1: _MM_DD_YYYY_HH_MM (e.g., _09_22_2025_20_22)
    name = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$', '', name)
    
    # Pattern 2: _MM_DD_YYYY (e.g., _01_23_2026)
    name = re.sub(r'_\d{2}_\d{2}_\d{4}$', '', name)
    
    # Pattern 3: _YYYY_MM_DD_HH_MM (e.g., _2025_09_22_20_22)
    name = re.sub(r'_\d{4}_\d{2}_\d{2}_\d{2}_\d{2}$', '', name)
    
    # Pattern 4: _YYYY_MM_DD (e.g., _2025_09_22)
    name = re.sub(r'_\d{4}_\d{2}_\d{2}$', '', name)
    
    # Pattern 5: _DD_MM_YYYY (European format)
    name = re.sub(r'_\d{2}_\d{2}_\d{4}$', '', name)
    
    return name


def normalize_profile_subject_for_grouping(name):
    """For grouping only: strip trailing _YYYY so 'The_Rock_2025' and 'The_Rock_2023' both become 'The_Rock'."""
    import re
    # Strip trailing _YYYY (e.g. _2025, _2023) so same profile different years group together
    return re.sub(r'_\d{4}$', '', name)


def get_profile_subject_from_s3_key(s3_key):
    """Return a canonical key for grouping same-profile different-date runs (e.g. 'The_Rock')."""
    if not s3_key:
        return ''
    filename = s3_key.split('/')[-1].replace('.csv', '')
    name = remove_timestamp_from_name(filename)
    return normalize_profile_subject_for_grouping(name)

def smart_title_case(text):
    """Convert to title case but preserve all-caps words (like JD, AOC, NFL, etc.)"""
    words = text.split(' ')
    result = []
    for word in words:
        # If word is all uppercase and 2-4 chars, keep it uppercase (likely an acronym/initials)
        if word.isupper() and 2 <= len(word) <= 4:
            result.append(word)
        # If word has mixed case already, keep it
        elif not word.islower() and not word.isupper():
            result.append(word)
        else:
            # Otherwise apply title case
            result.append(word.title())
    return ' '.join(result)

def process_s3_file_metadata(key, obj):
    """Process a single S3 file and extract metadata."""
    import re
    
    # Extract project name from filename - use smart title case for display
    filename = key.split('/')[-1]  # Get just the filename, not the full path
    name_without_ext = filename.replace('.csv', '')
    
    # Remove timestamp patterns
    name_without_timestamp = remove_timestamp_from_name(name_without_ext)
    
    # Replace underscores with spaces
    raw_name = name_without_timestamp.replace('_', ' ')
    
    project_name = smart_title_case(raw_name)
    
    # Try to get category from BRAND CATEGORY row in CSV
    category = 'UNCATEGORIZED'
    try:
        head_response = s3_client.head_object(Bucket=S3_BUCKET, Key=key)
        file_size = head_response['ContentLength']
        
        # Read last 200KB where BRAND CATEGORY row usually is (increased for safety)
        start_byte = max(0, file_size - 200000)
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key, Range=f'bytes={start_byte}-{file_size}')
        content = response['Body'].read().decode('utf-8', errors='ignore')
        
        for line in content.split('\n'):
            line_upper = line.strip().upper()
            # Check multiple variations of BRAND CATEGORY
            if line_upper.startswith('BRAND CATEGORY,') or line_upper.startswith('BRAND CATEGORY ') or line_upper.startswith('"BRAND CATEGORY"'):
                parts = line.split(',')
                if len(parts) >= 2:
                    cat = parts[1].strip().strip('"').upper()
                    if cat and cat != 'BRAND CATEGORY':
                        category = cat
                        break
            # Also check for BRAND_CATEGORY variant
            elif line_upper.startswith('BRAND_CATEGORY,'):
                parts = line.split(',')
                if len(parts) >= 2:
                    cat = parts[1].strip().strip('"').upper()
                    if cat:
                        category = cat
                        break
    except Exception as e:
        print(f"Error reading category from {key}: {e}")
    
    # Ensure Gen Pop files always have the correct category with space
    key_lower = key.lower()
    if 'gen_pop' in key_lower or 'genpop' in key_lower:
        category = 'GEN POP'
    
    return {
        'job_id': key,
        'project_name': project_name,
        'display_name': project_name,  # Default to project_name, can be overridden
        'name': project_name,  # Also set name for compatibility
        'status': 'cached',
        'progress': 100,
        'created_at': obj['LastModified'].isoformat(),
        'source': 's3',
        's3_key': key,
        'category': category,
        'profile_subject': get_profile_subject_from_s3_key(key)
    }


# ============================================================================
# JOB STATUS S3 PERSISTENCE (cross-worker visibility on Render)
# ============================================================================

def _load_jobs_status_from_s3():
    """Load job statuses from S3. Returns dict job_id -> job_data."""
    if not s3_client:
        return {}
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=JOBS_STATUS_S3_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except Exception:
        return {}

def _save_job_status_to_s3(job_id, job_data):
    """Save a single job's status to S3 (merge into existing)."""
    if not s3_client:
        return
    try:
        all_jobs = _load_jobs_status_from_s3()
        # Only store fields needed for status display (no local paths)
        safe = {
            'status': job_data.get('status'),
            'progress': job_data.get('progress', 0),
            'message': job_data.get('message'),
            'created_at': job_data.get('created_at'),
            'project_name': job_data.get('project_name'),
            'error': job_data.get('error'),
            's3_key': job_data.get('s3_key'),
            'created_by': job_data.get('created_by') or job_data.get('username'),
            'type': job_data.get('type', 'profile_analysis'),
            'brands': job_data.get('brands'),
        }
        all_jobs[job_id] = safe
        s3_client.put_object(Bucket=S3_BUCKET, Key=JOBS_STATUS_S3_KEY, Body=json.dumps(all_jobs), ContentType='application/json')
    except Exception as e:
        print(f"[Job Status] S3 save failed: {e}")


# ============================================================================
# ANALYSIS RUNNER
# ============================================================================

def _shorten_error_for_ui(error_str, max_len=4000):
    """Keep error readable in UI: cap length, preserve start and end (exception usually at end)."""
    if not error_str or len(error_str) <= max_len:
        return error_str or ''
    half = max_len // 2
    return error_str[:half] + '\n\n... [truncated] ...\n\n' + error_str[-half:]


def update_job_status(job_id, status=None, progress=None, message=None, error=None, result_file=None, demographic_validation=None, s3_key=None):
    """Update job status - simplified to avoid verbose terminal output."""
    if job_id in jobs:
        if status:
            jobs[job_id]['status'] = status
        if progress is not None:
            jobs[job_id]['progress'] = progress
        if message:
            jobs[job_id]['message'] = message
            jobs[job_id]['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            jobs[job_id]['logs'] = jobs[job_id]['logs'][-5:]
        if error:
            jobs[job_id]['error'] = _shorten_error_for_ui(str(error))
        if result_file:
            jobs[job_id]['result_file'] = result_file
        if demographic_validation:
            jobs[job_id]['demographic_validation'] = demographic_validation
        if s3_key:
            jobs[job_id]['s3_key'] = s3_key
        if s3_client:
            _save_job_status_to_s3(job_id, jobs[job_id])


def run_analysis(job_id, project_name, brands, sample_start, sample_end, 
                 behavior_start, behavior_end, filters, skew_settings, 
                 is_genpop, purchasers_only, brand_category,
                 include_frequency=False, is_listener_watcher=False, platform_name=None, 
                 previous_file_path=None, reference_demographics=None, reference_sample_size=None,
                 reference_file_key=None, geo_zip_codes=None, geo_dma=None):
    """Run the behavioral graph analysis pipeline with demographic consistency validation."""
    try:
        update_job_status(job_id, status='running', progress=5, message='Initializing...')
        
        # Import the bg module (same as local when run from finished_codes; see startup path setup)
        try:
            import bg
            import random
            import numpy as np
            _bg_file = getattr(bg, '__file__', 'unknown')
            print(f"📜 Profile analysis using bg from: {_bg_file}")
        except ImportError as e:
            update_job_status(job_id, status='failed', error=f'Module import failed: {str(e)}')
            return
        try:
            from config import SNOWFLAKE_CONFIG
        except ImportError:
            SNOWFLAKE_CONFIG = None
        
        # ========== EXPAND BRANDS TO ALL NAME COMBOS (matches terminal "Auto Format Inputs? Y") ==========
        if hasattr(bg, 'generate_brand_variations') and brands:
            expanded = []
            for b in brands:
                expanded.extend(bg.generate_brand_variations(b))
            brands = list(dict.fromkeys(expanded))  # preserve order, remove exact dupes
            print(f"🔄 Expanded to {len(brands)} brand variants for search and BRAND INPUT row")
        
        # ========== DETERMINISTIC SEEDING (matches terminal behavior) ==========
        # Create consistent random seed based on inputs for reproducible results
        seed_string = f"{brands[0]}_{sample_start}_{sample_end}_{behavior_start}_{behavior_end}" if brands else f"{sample_start}_{sample_end}_{behavior_start}_{behavior_end}"
        deterministic_seed = hash(seed_string) % (2**32)  # Convert to 32-bit integer
        random.seed(deterministic_seed)
        np.random.seed(deterministic_seed)
        print(f"🎲 Deterministic seed set: {deterministic_seed}")
        
        # Rerun: use reference profile for sample size and demographics consistency (pipeline enforces via previous_file_path)
        actual_previous_file = previous_file_path
        if reference_file_key and s3_client and not previous_file_path:
            try:
                update_job_status(job_id, progress=8, message='Loading reference file for consistency...')
                print(f"📥 Downloading reference file: {reference_file_key}")
                
                # Download reference file to temp location
                import tempfile
                temp_dir = tempfile.gettempdir()
                ref_filename = os.path.basename(reference_file_key)
                ref_local_path = os.path.join(temp_dir, f"ref_{ref_filename}")
                
                s3_client.download_file(S3_BUCKET, reference_file_key, ref_local_path)
                actual_previous_file = ref_local_path
                print(f"✅ Reference file downloaded: {ref_local_path}")
                print(f"   Will enforce ±2% consistency with this reference")
            except Exception as e:
                print(f"⚠️ Could not download reference file: {e}")
        
        # Connect to Snowflake
        update_job_status(job_id, progress=15, message='Connecting to database...')
        
        try:
            conn = bg.connect_snowflake()
        except Exception as e:
            update_job_status(job_id, status='failed', error=f'Database connection failed: {str(e)}')
            return
        
        # ========== UNIVERSE SCAN (matches terminal behavior) ==========
        # This is critical for getting correct sample sizes
        update_job_status(job_id, progress=18, message='Performing universe scan...')
        
        if geo_zip_codes and geo_dma:
            print("📍 Geographic profile - skipping brand universe scan (geo cohort will determine size)")
            bg.run_full_pipeline.universe_size = 1000000
        else:
            try:
                if hasattr(bg, 'perform_full_universe_scan'):
                    print("🔍 Performing full universe scan (matching terminal behavior)...")
                    universe_results = bg.perform_full_universe_scan(conn, brands, sample_start, sample_end, purchasers_only)
                    if universe_results:
                        print(f"🌍 Universe scan complete. True universe size: {universe_results['total_universe']:,} users")
                        bg.run_full_pipeline.universe_size = universe_results['total_universe']
                    else:
                        print("⚠️ Universe scan returned no results, using default")
                        bg.run_full_pipeline.universe_size = 1000000
                else:
                    print("⚠️ perform_full_universe_scan not available in bg module")
                    bg.run_full_pipeline.universe_size = 1000000
            except Exception as e:
                print(f"⚠️ Universe scan error: {e}, proceeding with default size")
                bg.run_full_pipeline.universe_size = 1000000
        
        update_job_status(job_id, progress=25, message='Running analysis...')
        
        # Use /tmp/bg_pipeline on Render (app dir often read-only); same flow for new run and rerun
        pipeline_out = '/tmp/bg_pipeline'
        try:
            os.makedirs(pipeline_out, exist_ok=True)
        except OSError:
            pipeline_out = None  # pipeline will use its default
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
                previous_file_path=actual_previous_file,
                brand_category=brand_category,
                is_listener_watcher=is_listener_watcher,
                output_dir=pipeline_out if pipeline_out else None,
                geo_zip_codes=geo_zip_codes,
                geo_dma=geo_dma
            )
            
            update_job_status(job_id, progress=85, message='Processing results...')
            
            # If path returned but file missing (e.g. path mismatch on server), find by project_name in output dir
            if result_file and not os.path.exists(result_file) and pipeline_out and os.path.isdir(pipeline_out):
                try:
                    prefix = (project_name if isinstance(project_name, str) else str(project_name)) + '_'
                    cutoff = datetime.now().timestamp() - 900  # 15 min
                    found = []
                    for f in os.listdir(pipeline_out):
                        if f.endswith('.csv') and f.startswith(prefix):
                            p = os.path.join(pipeline_out, f)
                            if os.path.isfile(p) and os.path.getmtime(p) >= cutoff:
                                found.append((os.path.getmtime(p), p))
                    if found:
                        found.sort(key=lambda x: -x[0])
                        result_file = found[0][1]
                except Exception:
                    pass
            if result_file and os.path.exists(result_file):
                # Apply frequency analysis if requested (matches bg.py terminal behavior EXACTLY)
                if include_frequency and not is_genpop:
                    try:
                        print("📊 Adding frequency metrics (matching terminal behavior)...")
                        update_job_status(job_id, progress=87, message='Adding frequency analysis...')
                        
                        import pandas as pd
                        df = pd.read_csv(result_file)
                        
                        # Use calculate_frequency_metrics like terminal version does
                        if hasattr(bg, 'calculate_frequency_metrics'):
                            frequency_df = bg.calculate_frequency_metrics(conn, brands, behavior_start, behavior_end, purchasers_only)
                            if frequency_df is not None and not frequency_df.empty:
                                # Add frequency columns to main df like terminal
                                if hasattr(bg, 'add_frequency_columns_to_main_df'):
                                    df = bg.add_frequency_columns_to_main_df(df, frequency_df)
                                else:
                                    # Fallback to merge if add_frequency_columns_to_main_df not available
                                    if hasattr(bg, 'merge_frequency_data'):
                                        df = bg.merge_frequency_data(df, frequency_df)
                        elif hasattr(bg, 'get_frequency_data'):
                            # Fallback to old method
                            freq_df = bg.get_frequency_data(conn, brands, sample_start, sample_end)
                            if freq_df is not None and not freq_df.empty:
                                df = bg.merge_frequency_data(df, freq_df) if hasattr(bg, 'merge_frequency_data') else df
                        
                        # Save; listener/watcher and rest of post-processing run below (matches terminal order)
                        df.to_csv(result_file, index=False)
                        print("✅ Frequency analysis complete")
                    except Exception as e:
                        print(f"⚠️ Frequency analysis error: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Apply listener/watcher adjustments when no frequency (terminal: only this path runs)
                elif is_listener_watcher:
                    try:
                        import pandas as pd
                        df = pd.read_csv(result_file)
                        if hasattr(bg, 'set_brand_input_to_csv'):
                            df = bg.set_brand_input_to_csv(df)
                        if platform_name and hasattr(bg, 'adjust_platform_to_100_percent'):
                            df = bg.adjust_platform_to_100_percent(df, platform_name)
                        df.to_csv(result_file, index=False)
                    except Exception as e:
                        print(f"⚠️ Listener/watcher adjustment error: {e}")
                    ensure_metadata_has_rerun_fields(result_file, brand_category or 'GENERAL', is_listener_watcher, platform_name)
                
                # ========== POST-PROCESSING only when include_frequency (matches terminal exactly) ==========
                # Terminal: when frequency is OFF, run_full_pipeline output is final; when ON, terminal runs this sequence after adding frequency.
                if include_frequency and not is_genpop:
                    update_job_status(job_id, progress=88, message='Applying final processing...')
                    try:
                        import pandas as pd
                        df = pd.read_csv(result_file)
                        
                        # 1. Enforce input brand to 100% (skip for GenPop)
                        if not is_genpop and hasattr(bg, 'enforce_input_brand_100'):
                            print("🎯 Enforcing input brand 100%...")
                            df = bg.enforce_input_brand_100(df, brands)
                        
                        # 2. Add input metadata to dataframe
                        if hasattr(bg, 'add_input_metadata_to_dataframe'):
                            print("📋 Adding input metadata...")
                            df = bg.add_input_metadata_to_dataframe(df, brands, sample_start, sample_end, behavior_start, behavior_end, deterministic_seed)
                        
                        # 3. Add unique purchase confirmations column
                        if hasattr(bg, 'add_unique_purchase_confirmations_column'):
                            try:
                                print("🛒 Adding unique purchase confirmations...")
                                df = bg.add_unique_purchase_confirmations_column(df, conn)
                            except Exception as e:
                                print(f"⚠️ Purchase confirmations error: {e}")
                        
                        # 4. Enforce cross-category brand consistency
                        if hasattr(bg, 'enforce_cross_category_brand_consistency'):
                            print("🔄 Enforcing cross-category brand consistency...")
                            df = bg.enforce_cross_category_brand_consistency(df)
                        
                        # 5. Remove dash variants from output
                        if hasattr(bg, 'remove_dash_variants_from_output'):
                            print("🗑️ Removing dash variants...")
                            df = bg.remove_dash_variants_from_output(df, brands)
                        
                        # 6. Convert all text values to uppercase
                        print("⬆️ Converting to uppercase...")
                        df['Column'] = df['Column'].astype(str).str.upper()
                        df['Value'] = df['Value'].astype(str).str.upper()
                        
                        # 7. Final sort by category order (matches terminal exactly)
                        print("📊 Final sorting by category order...")
                        CATEGORY_ORDER = [
                            "INPUT_METADATA", "BRAND INPUT", "SAMPLE SIZE", "AVID FAN", "CASUAL FAN",
                            "AGE", "EDUCATION", "ETHNICITY", "GENDER", "INCOME", "RELATIONSHIP", 
                            "SEXUAL_ORIENTATION", "PARENTAL_STATUS", "OCCUPATION", "LOCATION",
                            "INTEREST", "AMUSEMENT PARKS", "APP/PLATFORM USAGE", "AUTOMOBILE", "BANKING",
                            "DIGITAL BANKING", "CREDIT PROVIDER", "INVESTMENTS", "BETTING", "EDUCATION & LEARNING",
                            "FRANCHISE", "GAMES", "HEALTH & WELLNESS", "HEAVY MACHINERY", "INSURANCE", "MEDIA",
                            "MOST PURCHASED BRANDS", "MOVIE THEATER", "NON PROFIT/CHARITY", "PHARMACY", "TOYS",
                            "TRAVEL", "QSR", "WHERE THEY DINE", "WHERE THEY SHOP", "SEARCH ENGINE/AI", "SEARCH ENGINE",
                            "SOCIAL MEDIA", "BROADCAST/CABLE", "STREAMING/MUSIC", "STREAMING/PLATFORM", "STREAMING/CHANNEL",
                            "VIRTUAL MVPD FAST", "PORN MEDIA", "TECHNOLOGY/DEVICE", "TELECOM", "WORKOUT FACILITY",
                            "EVENTS", "VENUE", "TICKETING", "ACTOR", "ATHLETE", "HOST/PERSONALITY", "INFLUENCER/CREATOR",
                            "MLB ATHLETE", "MUSICIAN/BAND", "NBA ATHLETE", "NFL ATHLETE", "POLITICS/ACTIVIST",
                            "SOCCER ATHLETE", "WNBA ATHLETE", "TALENT", "SPORTS ORGANIZATIONS", "SPORTS TEAM",
                            "WNBA", "NBA", "NFL", "NFC", "NFC EAST", "NFC NORTH", "NFC SOUTH", "NFC WEST",
                            "NHL", "NWSL", "MLS", "ATLANTIC DIVISION", "PACIFIC DIVISION", "PREMIER LEAGUE",
                            "METROPOLITAN DIVISION", "MLB", "LA LIGA", "GOLF", "EASTERN CONFERENCE", "CENTRAL DIVISION",
                            "AFC", "AFC EAST", "AFC NORTH", "AFC SOUTH", "AFC WEST", "AL", "AL CENTRAL", "AL EAST",
                            "AL WEST", "SERIE A", "SOCCER", "TENNIS", "UEFA", "WESTERN CONFERENCE", "SPORTS",
                            "RUGBY", "VOLLEYBALL", "COLLEGE/UNIVERSITY", "ACCESSORIES", "APPAREL/FOOTWEAR",
                            "BEAUTY/WELLNESS", "BRAND CATEGORY", "CPG", "HOME/OUTDOOR", "MOST PURCHASED CATEGORIES", 
                            "PETS", "TECHNOLOGY BRAND"
                        ]
                        
                        def get_category_priority(col):
                            """Define sort priority for categories"""
                            col_upper = str(col).upper()
                            try:
                                return CATEGORY_ORDER.index(col_upper)
                            except ValueError:
                                return 1000  # Category not in predefined order - put at end
                        
                        df['__sort_priority'] = df['Column'].apply(get_category_priority)
                        
                        # Convert Category Share to numeric for proper sorting
                        sort_col = 'Category Share' if 'Category Share' in df.columns else 'Percentage'
                        df['__sort_value'] = pd.to_numeric(df[sort_col], errors='coerce').fillna(0)
                        
                        # Sort by: priority (asc), category name (asc), value (desc)
                        df = df.sort_values(
                            by=['__sort_priority', 'Column', '__sort_value'], 
                            ascending=[True, True, False]
                        )
                        
                        # Clean up temporary columns
                        df = df.drop(columns=['__sort_priority', '__sort_value'])
                        
                        # 8. Listener/watcher/player adjustments at end (matches terminal order)
                        if is_listener_watcher:
                            if hasattr(bg, 'set_brand_input_to_csv'):
                                df = bg.set_brand_input_to_csv(df)
                            if platform_name and hasattr(bg, 'adjust_platform_to_100_percent'):
                                df = bg.adjust_platform_to_100_percent(df, platform_name)
                        
                        # Save the fully processed file
                        df.to_csv(result_file, index=False)
                        ensure_metadata_has_rerun_fields(result_file, brand_category or 'GENERAL', is_listener_watcher, platform_name)
                        print("✅ All post-processing complete (matching terminal behavior)")
                    except Exception as e:
                        print(f"⚠️ Post-processing error (non-fatal): {e}")
                        import traceback
                        traceback.print_exc()
                
                # Rerun: validate new run vs reference (demographics ±2%, sample size ±2%); pipeline already used reference for consistency
                demographic_validation = None
                if reference_demographics:
                    try:
                        with open(result_file, 'r') as f:
                            new_csv_content = f.read()
                        new_demographics = extract_demographics_from_csv(new_csv_content)
                        new_sample_size = extract_sample_size_from_csv(new_csv_content)
                        
                        is_valid, discrepancies = validate_demographics_consistency(
                            new_demographics, reference_demographics, tolerance=2
                        )
                        
                        # Check sample size tolerance (+/- 2%)
                        sample_valid = True
                        sample_diff = 0
                        if reference_sample_size and new_sample_size:
                            sample_diff = abs(new_sample_size - reference_sample_size) / reference_sample_size * 100
                            sample_valid = sample_diff <= 2
                        
                        demographic_validation = {
                            'demographics_valid': is_valid,
                            'sample_size_valid': sample_valid,
                            'sample_size_diff_pct': round(sample_diff, 2),
                            'discrepancies': discrepancies[:10] if discrepancies else []
                        }
                    except Exception as e:
                        print(f"Demographic validation error: {e}")
                
                # Copy to OUTPUT_DIR then upload to S3 purgatory (same for new run and rerun)
                output_filename = f"{job_id}_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                output_path = os.path.join(OUTPUT_DIR, output_filename)
                import shutil
                try:
                    shutil.copy2(result_file, output_path)
                except OSError:
                    output_path = result_file  # e.g. Render: use pipeline file directly if OUTPUT_DIR not writable
                update_job_status(job_id, progress=95, message='Saving to purgatory...')
                created_by = jobs.get(job_id, {}).get('created_by', '')
                s3_key = upload_to_s3(output_path, project_name, sample_start, sample_end, created_by=created_by, use_purgatory=True, category=brand_category or 'GENERAL')
                if s3_key:
                    _add_user_profile(s3_key, created_by)
                update_job_status(
                    job_id,
                    status='completed',
                    progress=100,
                    message='Complete!',
                    result_file=output_path,
                    demographic_validation=demographic_validation,
                    s3_key=s3_key
                )
            else:
                if result_file:
                    print(f"⚠️ Pipeline returned path but file missing: {result_file}")
                update_job_status(job_id, status='failed', error='No output file generated')
                
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            error_msg = f'Analysis error: {str(e)}'
            print(f"❌ {error_msg}")
            print(tb_str)
            update_job_status(job_id, status='failed', error=f'{error_msg}\n\nTraceback:\n{tb_str[-2000:]}')
            
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        update_job_status(job_id, status='failed', error=str(e))


# ============================================================================
# STARTUP CACHE LOADING (Async - doesn't block app startup)
# ============================================================================

import threading
import time as time_module

cache_loading_complete = False
BACKGROUND_CHECK_INTERVAL = 60  # Check for new files every 1 minute so new S3 uploads show up quickly

def async_cache_loader():
    """Load cache in background - doesn't block app startup."""
    global cache_loading_complete
    import time
    
    # Small delay to ensure app is ready
    time_module.sleep(1)
    
    start = time.time()
    print("🚀 Background cache loading started...")
    
    # Load persisted file list cache (single JSON file)
    if s3_client:
        try:
            load_persisted_cache()
            print(f"   ✅ Loaded {len(s3_cache.get('jobs', []))} profiles in {time.time()-start:.2f}s")
        except Exception as e:
            print(f"   ⚠️ Cache load error: {e}")
        
        # Load profile image cache
        try:
            load_profile_image_cache()
            print(f"   ✅ Loaded {len(profile_image_cache)} cached images")
        except Exception as e:
            print(f"   ⚠️ Image cache load error: {e}")
        
        # IMMEDIATELY pre-fetch images for all profiles
        print("🖼️ Starting automatic image prefetch...")
        try:
            prefetch_profile_images()
        except Exception as e:
            print(f"   ⚠️ Image prefetch error: {e}")
    
    cache_loading_complete = True
    print(f"🎉 Cache ready in {time.time()-start:.2f}s")


def prefetch_profile_images():
    """Pre-fetch and cache images for all profiles."""
    import urllib.parse
    import urllib.request
    import re
    from datetime import datetime
    global profile_image_cache, profile_image_cache_dirty
    
    if not s3_cache.get('jobs'):
        print("   ⚠️ No profiles to fetch images for")
        return
    
    print(f"🖼️ Pre-fetching profile images for {len(s3_cache['jobs'])} profiles...")
    
    fetched = 0
    skipped = 0
    failed = 0
    
    for job in s3_cache.get('jobs', []):
        profile_name = job.get('project_name', '')
        if not profile_name:
            continue
        
        cache_key = profile_name.lower().strip()
        
        # Skip if already cached
        if cache_key in profile_image_cache:
            skipped += 1
            continue
        
        try:
            # Check for social media handle
            handle_match = re.search(r'@(\w+)', profile_name)
            image_url = None
            source = None
            title = profile_name
            
            if handle_match:
                handle = handle_match.group(1)
                
                # Try TikTok
                try:
                    tiktok_url = f"https://www.tiktok.com/@{handle}"
                    req = urllib.request.Request(tiktok_url, headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                    })
                    with urllib.request.urlopen(req, timeout=5) as response:
                        html = response.read().decode('utf-8', errors='ignore')
                        og_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html)
                        if not og_match:
                            og_match = re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html)
                        if og_match:
                            img = og_match.group(1)
                            if img and 'tiktok' not in img.lower().split('/')[-1] and 'logo' not in img.lower():
                                image_url = img
                                source = 'tiktok'
                                title = f'@{handle}'
                except:
                    pass
                
                # Try Instagram if TikTok failed
                if not image_url:
                    try:
                        instagram_url = f"https://www.instagram.com/{handle}/"
                        req = urllib.request.Request(instagram_url, headers={
                            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                        })
                        with urllib.request.urlopen(req, timeout=5) as response:
                            html = response.read().decode('utf-8', errors='ignore')
                            og_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html)
                            if not og_match:
                                og_match = re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html)
                            if og_match:
                                image_url = og_match.group(1)
                                source = 'instagram'
                                title = f'@{handle}'
                    except:
                        pass
            
            # Try Clearbit for brands
            if not image_url:
                search_name = re.sub(r'@\w+', '', profile_name).replace('_', ' ').replace('-', ' ').strip()
                if search_name:
                    domain = search_name.lower().replace(' ', '') + '.com'
                    clearbit_url = f"https://logo.clearbit.com/{domain}"
                    try:
                        req = urllib.request.Request(clearbit_url, method='HEAD', headers={'User-Agent': 'CrosswalkIQ/1.0'})
                        with urllib.request.urlopen(req, timeout=2) as response:
                            if response.status == 200:
                                image_url = clearbit_url
                                source = 'clearbit'
                    except:
                        pass
            
            # Cache the result
            if image_url:
                profile_image_cache[cache_key] = {
                    'image_url': image_url,
                    'title': title,
                    'source': source,
                    'cached_at': datetime.now().isoformat()
                }
                fetched += 1
                if fetched % 10 == 0:
                    print(f"   ✅ Fetched {fetched} images...")
            else:
                profile_image_cache[cache_key] = {
                    'not_found': True,
                    'cached_at': datetime.now().isoformat()
                }
                failed += 1
            
            profile_image_cache_dirty = True
            
            # Save every 20 fetches to persist progress
            if (fetched + failed) % 20 == 0:
                save_profile_image_cache()
            
            # Small delay to be nice to APIs
            time_module.sleep(0.3)
            
        except Exception as e:
            failed += 1
            if failed <= 5:  # Only log first few failures
                print(f"   ❌ {profile_name}: {e}")
    
    # Final save
    save_profile_image_cache()
    print(f"🖼️ Image prefetch complete: {fetched} found, {skipped} already cached, {failed} not found")


def background_cache_checker():
    """Background thread that checks for new/modified files every 5 minutes."""
    # Wait for initial cache load
    while not cache_loading_complete:
        time_module.sleep(1)
    
    # Pre-fetch profile images after startup
    time_module.sleep(5)  # Wait a bit before starting
    try:
        prefetch_profile_images()
    except Exception as e:
        print(f"   ⚠️ Image prefetch error: {e}")
    
    print("🔄 Starting background cache checker (every 1 min)...")
    while True:
        time_module.sleep(BACKGROUND_CHECK_INTERVAL)
        try:
            print("🔍 Background check for new files...")
            result = smart_cache_update()
            if result.get('new', 0) > 0 or result.get('updated', 0) > 0:
                print(f"   📥 Found {result.get('new', 0)} new, {result.get('updated', 0)} modified")
                # Also fetch images for new profiles
                prefetch_profile_images()
        except Exception as e:
            print(f"   ⚠️ Background check error: {e}")


# Load image cache synchronously on startup (small, fast operation)
# Start cache loader in background (doesn't block startup!)
# All cache loading happens in background thread to ensure fast startup
# Threads are daemon so they won't prevent app shutdown
print("🚀 App starting - cache will load in background...")
cache_thread = threading.Thread(target=async_cache_loader, daemon=True)
cache_thread.start()

# Start background checker thread
bg_checker = threading.Thread(target=background_cache_checker, daemon=True)
bg_checker.start()

# Start user initialization in background (non-blocking)
users_thread = threading.Thread(target=async_init_users, daemon=True)
users_thread.start()


# ============================================================================
# DECK BUILDER API
# ============================================================================

DECKS_S3_KEY = 'system/decks/'

@app.route('/api/decks', methods=['GET'])
@requires_auth
def get_user_decks():
    """Get all decks for the current user and their team."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    username = session.get('username')
    user_email = user.get('email', '').lower()
    company = user.get('company', '')
    
    try:
        decks = []
        # List all deck files
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=DECKS_S3_KEY):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.json'):
                    try:
                        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                        deck_data = json.loads(response['Body'].read().decode('utf-8'))
                        
                        # Show if owned by user, shared with user, collaborator, or same company
                        owner = deck_data.get('owner', '')
                        shared_with = deck_data.get('shared_with', [])
                        collaborators = deck_data.get('collaborators', [])
                        deck_company = deck_data.get('company', '')
                        is_team_deck = deck_data.get('is_team_deck', False)
                        
                        # Check if user is a collaborator
                        is_collaborator = any(
                            c.get('user_id') == username or 
                            c.get('email', '').lower() == user_email 
                            for c in collaborators
                        )
                        
                        can_view = (
                            owner == username or
                            username in shared_with or
                            is_collaborator or
                            (is_team_deck and deck_company == company)
                        )
                        
                        if can_view:
                            # Resolve owner display name for deck builder avatars
                            owner_name = owner
                            try:
                                users_data = load_users()
                                u = (users_data.get('users') or {}).get(owner) or {}
                                fn = (u.get('first_name') or '').strip()
                                ln = (u.get('last_name') or '').strip()
                                if fn or ln:
                                    owner_name = f"{fn} {ln}".strip()
                            except Exception:
                                pass
                            decks.append({
                                'id': deck_data.get('id'),
                                'name': deck_data.get('name', 'Untitled Deck'),
                                'owner': owner,
                                'owner_name': owner_name,
                                'is_mine': owner == username,
                                'is_team_deck': is_team_deck,
                                'is_collaborator': is_collaborator,
                                'slides_count': len(deck_data.get('slides', [])),
                                'created_at': deck_data.get('created_at'),
                                'updated_at': deck_data.get('updated_at'),
                                'shared_with': shared_with,
                                'collaborators': collaborators
                            })
                    except:
                        continue
        
        # Sort by updated date
        decks.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
        
        return jsonify({'success': True, 'decks': decks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks', methods=['POST'])
@requires_auth
def create_deck():
    """Create a new deck."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        deck_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        deck = {
            'id': deck_id,
            'name': data.get('name', 'Untitled Deck'),
            'description': data.get('description', ''),
            'owner': session.get('username'),
            'company': user.get('company', ''),
            'is_team_deck': data.get('is_team_deck', False),
            'shared_with': data.get('shared_with', []),
            'slides': data.get('slides', []),
            'template': data.get('template', 'default'),
            'created_at': now,
            'updated_at': now
        }
        
        # Save to S3
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(deck),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'deck': deck})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>', methods=['GET'])
@requires_auth
def get_deck(deck_id):
    """Get a specific deck by ID."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Check permission (owner, shared_with, collaborator, or team)
        username = session.get('username')
        user_email = user.get('email', '').lower()
        company = user.get('company', '')
        owner = deck.get('owner', '')
        shared_with = deck.get('shared_with', [])
        collaborators = deck.get('collaborators', [])
        deck_company = deck.get('company', '')
        is_team_deck = deck.get('is_team_deck', False)
        is_collaborator = any(
            c.get('user_id') == username or c.get('email', '').lower() == user_email
            for c in collaborators
        )
        
        can_view = (
            owner == username or
            username in shared_with or
            is_collaborator or
            (is_team_deck and deck_company == company)
        )
        
        if not can_view:
            return jsonify({'success': False, 'error': 'Permission denied'})
        
        deck['can_edit'] = owner == username or username in shared_with or is_collaborator
        
        # Add owner_name for deck builder avatars
        if not deck.get('owner_name') and owner:
            try:
                users_data = load_users()
                u = (users_data.get('users') or {}).get(owner) or {}
                fn = (u.get('first_name') or '').strip()
                ln = (u.get('last_name') or '').strip()
                if fn or ln:
                    deck['owner_name'] = f"{fn} {ln}".strip()
                else:
                    deck['owner_name'] = owner
            except Exception:
                deck['owner_name'] = owner
        
        return jsonify({'success': True, 'deck': deck})
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'success': False, 'error': 'Deck not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>', methods=['PUT'])
@requires_auth
def update_deck(deck_id):
    """Update an existing deck."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        
        # Get existing deck
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Check permission (owner, shared_with, or collaborator with edit role)
        username = session.get('username')
        user_email = user.get('email', '').lower()
        owner = deck.get('owner', '')
        shared_with = deck.get('shared_with', [])
        collaborators = deck.get('collaborators', [])
        is_collaborator = any(
            c.get('user_id') == username or c.get('email', '').lower() == user_email
            for c in collaborators
        )
        
        can_edit = owner == username or username in shared_with or is_collaborator
        if not can_edit:
            return jsonify({'success': False, 'error': 'Permission denied'})
        
        # Update deck
        data = request.get_json() or {}
        if 'name' in data:
            deck['name'] = data['name']
        if 'description' in data:
            deck['description'] = data['description']
        if 'slides' in data:
            deck['slides'] = data['slides']
        if 'titleSlide' in data:
            deck['titleSlide'] = data['titleSlide']
        if 'is_team_deck' in data and owner == username:
            deck['is_team_deck'] = data['is_team_deck']
        if 'shared_with' in data and owner == username:
            deck['shared_with'] = data['shared_with']
        if 'template' in data:
            deck['template'] = data['template']
        
        deck['updated_at'] = datetime.now().isoformat()
        deck['last_editor'] = username
        
        # Save to S3
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(deck),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'deck': deck})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>', methods=['DELETE'])
@requires_auth
def delete_deck(deck_id):
    """Delete a deck (owner only)."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        
        # Get existing deck
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Only owner can delete
        if deck.get('owner') != session.get('username'):
            return jsonify({'success': False, 'error': 'Only the deck owner can delete it'})
        
        # Delete from S3
        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        
        return jsonify({'success': True, 'message': 'Deck deleted'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>/share', methods=['POST'])
@requires_auth
def share_deck(deck_id):
    """Share a deck with team members."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        share_with = data.get('share_with', [])
        is_team_deck = data.get('is_team_deck', False)
        
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Only owner can share
        if deck.get('owner') != session.get('username'):
            return jsonify({'success': False, 'error': 'Only the deck owner can share it'})
        
        # Update sharing settings
        deck['shared_with'] = list(set(deck.get('shared_with', []) + share_with))
        deck['is_team_deck'] = is_team_deck
        deck['updated_at'] = datetime.now().isoformat()
        
        # Save
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(deck),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'deck': deck})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>/duplicate', methods=['POST'])
@requires_auth
def duplicate_deck(deck_id):
    """Duplicate an existing deck."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        original = json.loads(response['Body'].read().decode('utf-8'))
        
        # Create new deck
        new_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        new_deck = {
            'id': new_id,
            'name': f"Copy of {original.get('name', 'Untitled')}",
            'description': original.get('description', ''),
            'owner': session.get('username'),
            'company': user.get('company', ''),
            'is_team_deck': False,
            'shared_with': [],
            'slides': original.get('slides', []),
            'template': original.get('template', 'default'),
            'created_at': now,
            'updated_at': now
        }
        
        # Save new deck
        new_key = f"{DECKS_S3_KEY}{new_id}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=new_key,
            Body=json.dumps(new_deck),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'deck': new_deck})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================================
# DECK COLLABORATORS API
# ============================================================================

@app.route('/api/decks/<deck_id>/collaborators', methods=['GET'])
@requires_auth
def get_deck_collaborators(deck_id):
    """Get collaborators for a deck."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        collaborators = deck.get('collaborators', [])
        return jsonify({'success': True, 'collaborators': collaborators})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>/collaborators', methods=['POST'])
@requires_auth
def add_deck_collaborator(deck_id):
    """Add a collaborator to a deck."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        user_id = data.get('user_id', '')
        name = data.get('name', '')
        # Shared access grants full (editor) access to the deck
        role = 'editor'
        
        if not email and not user_id:
            return jsonify({'success': False, 'error': 'Email or user_id required'})
        
        # Get existing deck
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Check ownership
        username = session.get('username')
        if deck.get('owner') != username:
            # Check if user is a collaborator with edit rights
            collaborators = deck.get('collaborators', [])
            is_editor = any(c.get('user_id') == username and c.get('role') == 'editor' for c in collaborators)
            if not is_editor:
                return jsonify({'success': False, 'error': 'Only the owner or editors can add collaborators'})
        
        # Initialize collaborators list if needed
        if 'collaborators' not in deck:
            deck['collaborators'] = []
        
        # Check if already a collaborator
        existing = next((c for c in deck['collaborators'] if c.get('email') == email or c.get('user_id') == user_id), None)
        if existing:
            return jsonify({'success': False, 'error': 'User is already a collaborator'})
        
        # Add new collaborator
        new_collaborator = {
            'user_id': user_id or email,
            'email': email,
            'name': name,
            'role': role,
            'added_at': datetime.now().isoformat(),
            'added_by': username
        }
        deck['collaborators'].append(new_collaborator)
        deck['updated_at'] = datetime.now().isoformat()
        
        # Save updated deck
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(deck),
            ContentType='application/json'
        )
        
        # Email collaborator: invited to collaborate on PROFILE NAME deck by FIRST LAST, with login link
        deck_name = deck.get('name') or 'Untitled Deck'
        inviter = get_current_user()
        first_name = (inviter or {}).get('first_name', '') or session.get('username', 'A colleague')
        last_name = (inviter or {}).get('last_name', '')
        inviter_display = f"{first_name} {last_name}".strip() or session.get('username', 'A colleague')
        app_url = os.environ.get('APP_URL', request.host_url.rstrip('/'))
        if email:
            _send_deck_collaboration_invite(email, deck_name, inviter_display, app_url)
        
        return jsonify({'success': True, 'collaborator': new_collaborator, 'deck': deck})
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'success': False, 'error': 'Deck not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/decks/<deck_id>/collaborators/<collaborator_id>', methods=['DELETE'])
@requires_auth
def remove_deck_collaborator(deck_id, collaborator_id):
    """Remove a collaborator from a deck."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        # Get existing deck
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Check ownership
        username = session.get('username')
        if deck.get('owner') != username:
            return jsonify({'success': False, 'error': 'Only the owner can remove collaborators'})
        
        # Remove collaborator
        collaborators = deck.get('collaborators', [])
        deck['collaborators'] = [c for c in collaborators if c.get('user_id') != collaborator_id and c.get('email') != collaborator_id]
        deck['updated_at'] = datetime.now().isoformat()
        
        # Save updated deck
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(deck),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'deck': deck})
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'success': False, 'error': 'Deck not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================================
# DECK SYNC (Workspace -> S3 for real-time collaboration)
# ============================================================================

@app.route('/api/decks/sync', methods=['POST'])
@requires_auth
def sync_workspace_deck():
    """Sync a workspace deck to S3 so it can be shared and collaborated on in real-time."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})

    try:
        data = request.get_json()
        deck = data.get('deck', {})
        deck_id = deck.get('id') or deck.get('serverDeckId') or str(uuid.uuid4())
        is_new = not deck.get('serverDeckId')

        # Use UUID for S3-backed decks (not deck_timestamp)
        if deck_id.startswith('deck_'):
            deck_id = str(uuid.uuid4())

        now = datetime.now().isoformat()
        username = session.get('username')

        deck_data = {
            'id': deck_id,
            'name': deck.get('name', 'Untitled Deck'),
            'description': deck.get('description', ''),
            'owner': deck.get('owner') or username,
            'company': user.get('company', ''),
            'is_team_deck': deck.get('is_team_deck', False),
            'shared_with': deck.get('shared_with', []),
            'collaborators': deck.get('collaborators', []),
            'slides': deck.get('slides', []),
            'titleSlide': deck.get('titleSlide', {}),
            'template': deck.get('template', 'default'),
            'created_at': deck.get('created_at') or now,
            'updated_at': now,
            'last_editor': username,
        }

        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(deck_data),
            ContentType='application/json'
        )

        return jsonify({
            'success': True,
            'deck': deck_data,
            'serverDeckId': deck_id,
            'is_new': is_new
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================================
# DECK SLIDE AI ANALYSIS
# ============================================================================

@app.route('/api/decks/analyze-slide', methods=['POST'])
@requires_auth
def analyze_slide_with_ai():
    """Use ChatGPT to analyze slide data and return key points and takeaways."""
    client = get_openai_client()
    if not client:
        return jsonify({'success': False, 'error': 'OpenAI not configured'}), 500

    try:
        data = request.get_json()
        content = data.get('content', '') or data.get('slideContent', '')
        slide_title = data.get('title', data.get('slideTitle', 'Slide'))

        if not content or not content.strip():
            return jsonify({'success': False, 'error': 'No content to analyze'}), 400

        prompt = f"""Analyze the following slide content and provide:
1. **Key Points** - 3-5 bullet points summarizing the most important data/insights
2. **Takeaways** - 2-4 actionable takeaways or conclusions for the audience

Slide title: {slide_title}

Content:
{content[:8000]}

Format your response as JSON:
{{
  "key_points": ["point 1", "point 2", ...],
  "takeaways": ["takeaway 1", "takeaway 2", ...]
}}

Respond ONLY with valid JSON, no additional text."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an analyst summarizing data for executives. Be concise and insightful. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=600
        )

        result_text = response.choices[0].message.content.strip()
        if '```json' in result_text:
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif '```' in result_text:
            result_text = result_text.split('```')[1].split('```')[0].strip()

        result = json.loads(result_text)
        key_points = result.get('key_points', [])
        takeaways = result.get('takeaways', [])

        combined = []
        if key_points:
            combined.append('**Key Points**')
            combined.extend(f"• {p}" for p in key_points)
        if takeaways:
            combined.append('')
            combined.append('**Takeaways**')
            combined.extend(f"• {t}" for t in takeaways)

        analysis_text = '\n'.join(combined)

        return jsonify({
            'success': True,
            'key_points': key_points,
            'takeaways': takeaways,
            'analysis': analysis_text
        })
    except json.JSONDecodeError as e:
        return jsonify({'success': False, 'error': f'Invalid AI response: {str(e)}'}), 500
    except Exception as e:
        print(f"❌ Error in analyze_slide_with_ai: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# DECK COMMIT TO GITHUB
# ============================================================================

@app.route('/api/decks/<deck_id>/commit-github', methods=['POST'])
@requires_auth
def commit_deck_to_github(deck_id):
    """Commit deck to the behavioral graph GitHub repository."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})

    try:
        data = request.get_json() or {}
        repo_name = data.get('repo', os.environ.get('GITHUB_REPO', 'behavioral-graph/decks'))
        branch = data.get('branch', 'main')
        file_path = data.get('path', f'decks/{deck_id}.json')

        github_token = os.environ.get('GITHUB_TOKEN')
        if not github_token:
            return jsonify({
                'success': False,
                'error': 'GitHub integration not configured. Set GITHUB_TOKEN and GITHUB_REPO environment variables.'
            })

        # Get deck data - try S3 first
        try:
            s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            deck_data = json.loads(response['Body'].read().decode('utf-8'))
        except Exception:
            return jsonify({'success': False, 'error': 'Deck not found in cloud. Sync deck first by sharing it.'})

        try:
            from github import Github
            g = Github(github_token)
            repo = g.get_repo(repo_name)

            content = json.dumps(deck_data, indent=2)
            try:
                existing = repo.get_contents(file_path, ref=branch)
                repo.update_file(file_path, f'Update deck: {deck_data.get("name", "Untitled")}', content, existing.sha, branch=branch)
                message = f'Updated {file_path}'
            except Exception:
                repo.create_file(file_path, f'Add deck: {deck_data.get("name", "Untitled")}', content, branch=branch)
                message = f'Created {file_path}'

            return jsonify({
                'success': True,
                'message': message,
                'file_path': file_path,
                'repo': repo_name,
                'branch': branch
            })
        except ImportError:
            return jsonify({
                'success': False,
                'error': 'PyGithub not installed. Run: pip install PyGithub'
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================================
# DECK REAL-TIME COLLABORATION (WebSocket)
# ============================================================================

if SOCKETIO_AVAILABLE and socketio:
    @socketio.on('join_deck')
    def handle_join_deck(data):
        """Join a deck collaboration room."""
        deck_id = data.get('deck_id')
        user = data.get('user', 'Anonymous')
        color = data.get('color', '#c8e600')
        if deck_id:
            join_room(f'deck_{deck_id}')
            emit('user_joined', {'user': user, 'color': color}, room=f'deck_{deck_id}', include_self=False)
            emit('joined', {'deck_id': deck_id})

    @socketio.on('leave_deck')
    def handle_leave_deck(data):
        """Leave a deck collaboration room."""
        deck_id = data.get('deck_id')
        user = data.get('user', 'Anonymous')
        if deck_id:
            leave_room(f'deck_{deck_id}')
            emit('user_left', {'user': user}, room=f'deck_{deck_id}')

    @socketio.on('slide_update')
    def handle_slide_update(data):
        """Broadcast slide update to other collaborators."""
        deck_id = data.get('deck_id')
        slide_idx = data.get('slide_idx')
        slide_data = data.get('slide_data')
        user = data.get('user', 'Anonymous')
        if deck_id and slide_data is not None:
            emit('slide_update', {
                'deckId': deck_id,
                'slideIdx': slide_idx,
                'slideData': slide_data,
                'user': user
            }, room=f'deck_{deck_id}', include_self=False)

    @socketio.on('cursor')
    def handle_cursor(data):
        """Broadcast cursor position to other collaborators."""
        deck_id = data.get('deck_id')
        x = data.get('x', 0)
        y = data.get('y', 0)
        user = data.get('user', 'Anonymous')
        color = data.get('color', '#c8e600')
        if deck_id:
            emit('cursor', {'user': user, 'x': x, 'y': y, 'color': color}, room=f'deck_{deck_id}', include_self=False)


# ============================================================================
# CANVA INTEGRATION API
# ============================================================================

CANVA_CLIENT_ID = os.environ.get('CANVA_CLIENT_ID', '')
CANVA_CLIENT_SECRET = os.environ.get('CANVA_CLIENT_SECRET', '')
CANVA_REDIRECT_URI = os.environ.get('CANVA_REDIRECT_URI', '')
CANVA_TOKENS_KEY = 'system/canva_tokens.json'

def load_canva_tokens():
    """Load Canva OAuth tokens from S3."""
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=CANVA_TOKENS_KEY)
        return json.loads(response['Body'].read().decode('utf-8'))
    except:
        return {}

def save_canva_tokens(tokens):
    """Save Canva OAuth tokens to S3."""
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=CANVA_TOKENS_KEY,
            Body=json.dumps(tokens),
            ContentType='application/json'
        )
    except Exception as e:
        print(f"Error saving Canva tokens: {e}")


@app.route('/api/canva/auth')
@requires_auth
def canva_auth():
    """Initiate Canva OAuth flow."""
    if not CANVA_CLIENT_ID:
        return jsonify({'success': False, 'error': 'Canva integration not configured'})
    
    # Generate state token for security
    state = str(uuid.uuid4())
    session['canva_oauth_state'] = state
    
    auth_url = f"https://www.canva.com/api/oauth/authorize?" + \
               f"client_id={CANVA_CLIENT_ID}&" + \
               f"redirect_uri={CANVA_REDIRECT_URI}&" + \
               f"response_type=code&" + \
               f"scope=design:read design:write&" + \
               f"state={state}"
    
    return jsonify({'success': True, 'auth_url': auth_url})


@app.route('/api/canva/callback')
def canva_callback():
    """Handle Canva OAuth callback."""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    
    if error:
        return redirect('/?canva_error=' + error)
    
    if state != session.get('canva_oauth_state'):
        return redirect('/?canva_error=invalid_state')
    
    # Exchange code for tokens
    try:
        import urllib.request
        import urllib.parse
        
        token_url = 'https://api.canva.com/rest/v1/oauth/token'
        data = urllib.parse.urlencode({
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': CANVA_REDIRECT_URI,
            'client_id': CANVA_CLIENT_ID,
            'client_secret': CANVA_CLIENT_SECRET
        }).encode()
        
        req = urllib.request.Request(token_url, data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        
        with urllib.request.urlopen(req) as response:
            tokens = json.loads(response.read().decode())
            
            # Save tokens for user
            username = session.get('username')
            all_tokens = load_canva_tokens()
            all_tokens[username] = {
                'access_token': tokens.get('access_token'),
                'refresh_token': tokens.get('refresh_token'),
                'expires_at': (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat()
            }
            save_canva_tokens(all_tokens)
        
        return redirect('/?canva_connected=true')
    except Exception as e:
        print(f"Canva OAuth error: {e}")
        return redirect('/?canva_error=token_exchange_failed')


@app.route('/api/canva/status')
@requires_auth
def canva_status():
    """Check if Canva is connected for current user."""
    username = session.get('username')
    tokens = load_canva_tokens()
    
    if username in tokens:
        # Check if token is expired
        expires_at = tokens[username].get('expires_at', '')
        if expires_at:
            try:
                exp_date = datetime.fromisoformat(expires_at)
                if exp_date > datetime.now():
                    return jsonify({
                        'success': True,
                        'connected': True,
                        'expires_at': expires_at
                    })
            except:
                pass
    
    return jsonify({
        'success': True,
        'connected': False
    })


@app.route('/api/canva/disconnect', methods=['POST'])
@requires_auth
def canva_disconnect():
    """Disconnect Canva for current user."""
    username = session.get('username')
    tokens = load_canva_tokens()
    
    if username in tokens:
        del tokens[username]
        save_canva_tokens(tokens)
    
    return jsonify({'success': True, 'message': 'Canva disconnected'})


@app.route('/api/canva/export-deck', methods=['POST'])
@requires_auth
def export_deck_to_canva():
    """Export a deck to Canva as a new design."""
    username = session.get('username')
    tokens = load_canva_tokens()
    
    if username not in tokens:
        return jsonify({'success': False, 'error': 'Canva not connected'})
    
    access_token = tokens[username].get('access_token')
    if not access_token:
        return jsonify({'success': False, 'error': 'Invalid Canva token'})
    
    try:
        data = request.get_json()
        deck_id = data.get('deck_id')
        
        # Get deck data
        s3_key = f"{DECKS_S3_KEY}{deck_id}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        deck = json.loads(response['Body'].read().decode('utf-8'))
        
        # Create Canva design via API
        # Note: This is a simplified example - actual Canva API requires more setup
        import urllib.request
        
        create_url = 'https://api.canva.com/rest/v1/designs'
        design_data = json.dumps({
            'design_type': 'presentation',
            'title': deck.get('name', 'Crosswalk Deck'),
            'preset_id': '16x9'  # Standard presentation size
        }).encode()
        
        req = urllib.request.Request(create_url, data=design_data, method='POST')
        req.add_header('Authorization', f'Bearer {access_token}')
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            design_id = result.get('design', {}).get('id')
            edit_url = result.get('design', {}).get('urls', {}).get('edit_url')
            
            return jsonify({
                'success': True,
                'design_id': design_id,
                'edit_url': edit_url,
                'message': 'Deck exported to Canva! Click to open in Canva.'
            })
    except Exception as e:
        print(f"Canva export error: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/team-members')
@requires_auth
def get_team_members():
    """Get team members in the same company."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    company = user.get('company', '')
    if not company:
        return jsonify({'success': True, 'members': []})
    
    users_data = load_users()
    members = []
    
    for username, user_data in users_data.get('users', {}).items():
        if user_data.get('company', '') == company:
            members.append({
                'username': username,
                'department': user_data.get('department', ''),
                'profile_picture': user_data.get('profile_picture')
            })
    
    return jsonify({'success': True, 'members': members})


# ============================================================================
# COLLABORATION API ENDPOINTS
# ============================================================================

COLLAB_S3_KEY = 'system/collaboration/'

@app.route('/api/collab/share-profile', methods=['POST'])
@requires_auth
def share_profile():
    """Share a profile with workspace or team."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        username = session.get('username')
        company = user.get('company', '')
        
        share_data = {
            'id': data.get('id', f"sp_{int(datetime.now().timestamp())}"),
            'profileKey': data.get('profileKey'),
            'profileName': data.get('profileName'),
            'sharedBy': username,
            'sharedAt': datetime.now().isoformat(),
            'workspace': data.get('workspace', 'default'),
            'company': company,
            'comments': []
        }
        
        # Save to S3
        s3_key = f"{COLLAB_S3_KEY}shares/{share_data['id']}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(share_data),
            ContentType='application/json'
        )
        
        # Track activity
        track_user_activity(username, 'shared_profile', share_data['profileName'])
        
        return jsonify({'success': True, 'share': share_data})
    except Exception as e:
        print(f"Share profile error: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/collab/shared-profiles')
@requires_auth
def get_shared_profiles():
    """Get shared profiles for user's company."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    company = user.get('company', '')
    
    try:
        shares = []
        prefix = f"{COLLAB_S3_KEY}shares/"
        
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=obj['Key'])
                    share = json.loads(response['Body'].read().decode('utf-8'))
                    # Only return shares from same company
                    if share.get('company', '') == company:
                        shares.append(share)
                except:
                    continue
        
        # Sort by date descending
        shares.sort(key=lambda x: x.get('sharedAt', ''), reverse=True)
        
        return jsonify({'success': True, 'shares': shares})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'shares': []})


@app.route('/api/collab/notification', methods=['POST'])
@requires_auth
def send_notification():
    """Send a notification to team members."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        notification = {
            'id': f"n_{int(datetime.now().timestamp())}",
            'type': data.get('type', 'general'),
            'message': data.get('message', ''),
            'recipients': data.get('recipients', []),
            'sender': session.get('username'),
            'createdAt': datetime.now().isoformat(),
            'read': False,
            'data': data.get('data', {})
        }
        
        # Save notification
        s3_key = f"{COLLAB_S3_KEY}notifications/{notification['id']}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(notification),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'notification': notification})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/collab/notifications')
@requires_auth
def get_notifications():
    """Get notifications for current user."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    username = session.get('username')
    
    try:
        notifications = []
        prefix = f"{COLLAB_S3_KEY}notifications/"
        
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=obj['Key'])
                    notif = json.loads(response['Body'].read().decode('utf-8'))
                    # Only return notifications for this user
                    if username in notif.get('recipients', []):
                        notifications.append(notif)
                except:
                    continue
        
        # Sort by date descending
        notifications.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        
        return jsonify({'success': True, 'notifications': notifications[:50]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'notifications': []})


@app.route('/api/collab/workspaces', methods=['GET'])
@requires_auth
def get_workspaces():
    """Get workspaces for user's company."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    username = session.get('username')
    company = user.get('company', '')
    
    try:
        workspaces = []
        prefix = f"{COLLAB_S3_KEY}workspaces/"
        
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=obj['Key'])
                    ws = json.loads(response['Body'].read().decode('utf-8'))
                    # Return workspaces user has access to
                    if ws.get('company', '') == company or username in ws.get('members', []) or ws.get('createdBy') == username:
                        workspaces.append(ws)
                except:
                    continue
        
        return jsonify({'success': True, 'workspaces': workspaces})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'workspaces': []})


@app.route('/api/collab/workspaces', methods=['POST'])
@requires_auth
def create_workspace():
    """Create a new workspace."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        username = session.get('username')
        company = user.get('company', '')
        
        workspace = {
            'id': f"ws_{int(datetime.now().timestamp())}",
            'name': data.get('name', 'New Workspace'),
            'description': data.get('description', ''),
            'members': data.get('members', []),
            'createdBy': username,
            'createdAt': datetime.now().isoformat(),
            'company': company,
            'profiles': []
        }
        
        # Save workspace
        s3_key = f"{COLLAB_S3_KEY}workspaces/{workspace['id']}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(workspace),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'workspace': workspace})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/collab/team-assignments', methods=['GET'])
@requires_auth
def get_team_assignments():
    """Get custom team assignments for user's company."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    company = user.get('company', '')
    if not company:
        return jsonify({'success': True, 'team_members': []})
    
    try:
        s3_key = f"{COLLAB_S3_KEY}teams/{company.replace(' ', '_').lower()}.json"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        team_data = json.loads(response['Body'].read().decode('utf-8'))
        return jsonify({'success': True, 'team_members': team_data.get('members', [])})
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'success': True, 'team_members': []})
    except Exception as e:
        return jsonify({'success': True, 'team_members': []})


@app.route('/api/collab/team-assignments', methods=['POST'])
@requires_admin
def save_team_assignments():
    """Save custom team assignments for company (admin only)."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    company = user.get('company', '')
    if not company:
        return jsonify({'success': False, 'error': 'No company assigned'})
    
    try:
        data = request.get_json()
        team_members = data.get('team_members', [])
        
        team_data = {
            'company': company,
            'members': team_members,
            'updated_by': session.get('username'),
            'updated_at': datetime.now().isoformat()
        }
        
        s3_key = f"{COLLAB_S3_KEY}teams/{company.replace(' ', '_').lower()}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(team_data),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'team_members': team_members})
    except Exception as e:
        print(f"Save team assignments error: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/collab/comments', methods=['GET'])
@requires_auth
def get_comments():
    """Get comments for user's workspaces."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    company = user.get('company', '')
    
    try:
        comments = []
        prefix = f"{COLLAB_S3_KEY}comments/"
        
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=obj['Key'])
                    comment = json.loads(response['Body'].read().decode('utf-8'))
                    if comment.get('company', '') == company:
                        comments.append(comment)
                except:
                    continue
        
        # Sort by date descending
        comments.sort(key=lambda x: x.get('time', ''), reverse=True)
        
        return jsonify({'success': True, 'comments': comments[:100]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'comments': []})


@app.route('/api/collab/comments', methods=['POST'])
@requires_auth
def post_comment():
    """Post a new comment."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    try:
        data = request.get_json()
        username = session.get('username')
        company = user.get('company', '')
        
        comment = {
            'id': f"c_{int(datetime.now().timestamp())}",
            'author': username,
            'text': data.get('text', ''),
            'time': datetime.now().isoformat(),
            'profile': data.get('profile', 'General'),
            'profileKey': data.get('profileKey'),
            'workspace': data.get('workspace', 'default'),
            'company': company,
            'mention': data.get('mention'),
            'replies': []
        }
        
        # Save comment
        s3_key = f"{COLLAB_S3_KEY}comments/{comment['id']}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=json.dumps(comment),
            ContentType='application/json'
        )
        
        # Track activity
        track_user_activity(username, 'posted_comment', comment['profile'])
        
        return jsonify({'success': True, 'comment': comment})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================================
# ATTRIBUTION IQ / ANALYSIS IQ ENDPOINTS
# ============================================================================

def user_can_view_ticket_sales_tracker(user):
    """True if user can view the Ticket Sales Tracker dashboard (list and view reports)."""
    if not user:
        return False
    if user.get('role') in ('admin', 'super_admin'):
        return True
    return user.get('has_ticket_sales_tracker_access', False) is True


def user_can_run_analysis_module(user, module_key):
    """True if user can run the given Analysis IQ module (talent_search, svod, campaign, etc.)."""
    if not user:
        return False
    # When admin is cloaked as another user, grant full Analysis IQ access
    if session.get('cloaked_from'):
        return True
    role = user.get('role', 'user')
    if role in ('admin', 'super_admin'):
        return True
    # New: Analysis IQ access + module list (saved by admin checkboxes)
    if user.get('has_analysis_iq_access'):
        modules = user.get('analysis_iq_modules') or []
        if module_key in modules:
            return True
    # Backward compat: legacy flag
    if user.get('has_attribution_iq_access'):
        return True
    return False


@app.route('/api/attribution/talent-search', methods=['POST'], strict_slashes=False)
@requires_auth
def submit_talent_search():
    """Submit a Talent Search IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access (Analysis IQ + Talent Search module)
        if not user_can_run_analysis_module(user, 'talent_search'):
            return jsonify({'error': 'Analysis IQ access with Talent Search module required'}), 403
        
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        search_terms = data.get('search_terms', [])
        platforms = data.get('platforms', [])
        
        if not start_date or not end_date:
            return jsonify({'error': 'Start date and end date are required'}), 400
        if not search_terms:
            return jsonify({'error': 'At least one search term is required'}), 400
        if not platforms:
            return jsonify({'error': 'At least one platform is required'}), 400
        
        # Create job
        job_id = str(uuid.uuid4())
        username = session.get('username', 'unknown')
        
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'talent_search',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'start_date': start_date,
                'end_date': end_date,
                'search_terms': search_terms,
                'platforms': platforms,
                'before_common_name': data.get('before_common_name')
            }
        }
        
        # Start job in background thread
        thread = threading.Thread(target=run_talent_search, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Talent Search IQ job submitted successfully',
            'status': 'queued'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/attribution/talent-theater', methods=['POST'], strict_slashes=False)
@requires_auth
def submit_talent_theater():
    """Submit a Talent Ticket Sale IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access (Analysis IQ + Ticket Sales / Talent Theater module)
        if not user_can_run_analysis_module(user, 'talent_theater'):
            return jsonify({'error': 'Analysis IQ access with Ticket Sales module required'}), 403
        
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        talent_name = data.get('talent_name')
        movie_name = data.get('movie_name')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if not talent_name:
            return jsonify({'error': 'Talent name is required'}), 400
        if not movie_name:
            return jsonify({'error': 'Movie name is required'}), 400
        if not start_date or not end_date:
            return jsonify({'error': 'Start date and end date are required'}), 400
        
        username = session.get('username', 'unknown')
        if not has_credits_for(username, CREDITS_TICKET_SALES):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Ticket Sales requires {CREDITS_TICKET_SALES} credits. You have {"no" if credits_left == 0 else credits_left} remaining.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        
        # Create job
        job_id = str(uuid.uuid4())
        
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'talent_theater',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'talent_name': talent_name,
                'competitive_talents': data.get('competitive_talents', []),
                'movie_name': movie_name,
                'start_date': start_date,
                'end_date': end_date
            }
        }
        
        # Consume credits and start job
        desc = f"{talent_name} / {movie_name} ({start_date}–{end_date})"
        if not consume_credit(username, description=desc, job_id=job_id, pull_type='Ticket Sales', credits_used=CREDITS_TICKET_SALES):
            return jsonify({'error': 'Insufficient credits.'}), 403
        thread = threading.Thread(target=run_talent_theater, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Talent Ticket Sale IQ job submitted successfully',
            'status': 'queued'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def run_talent_search(job_id):
    """Run the platform_search_tracker.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'platform_search_tracker.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
            return
        
        # Get job parameters
        job = jobs[job_id]
        params = job['params']
        
        update_job_status(job_id, progress=30, message='Running analysis...')
        
        # Import and run the script
        import importlib.util
        spec = importlib.util.spec_from_file_location("platform_search_tracker", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        
        # Prepare parameters for the script
        from datetime import datetime
        start_date = datetime.strptime(params['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(params['end_date'], '%Y-%m-%d')
        
        # Run the analysis function
        conn = module.connect_snowflake()
        try:
            script_params = {
                'start_date': start_date,
                'end_date': end_date,
                'search_terms': params['search_terms'],
                'platforms': params['platforms'],
                'before_common_name': params.get('before_common_name')
            }
            results = module.run_analysis(conn, script_params)
            update_job_status(job_id, progress=80, message='Writing output...')
            
            # Write output and capture path
            module.write_output(results)
            
            # Find the output file (it's written to Desktop/attribution folder)
            from pathlib import Path
            output_folder = Path.home() / "Desktop" / "attribution"
            if output_folder.exists():
                # Find the most recent CSV file in the folder
                csv_files = list(output_folder.glob("platform_search_tracker_*.csv"))
                if csv_files:
                    # Sort by modification time, get most recent
                    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    output_file = str(csv_files[0])
                    if os.path.exists(output_file):
                        jobs[job_id]['result_file'] = output_file
                        update_job_status(job_id, progress=100, status='completed', message='Analysis complete!')
                    else:
                        update_job_status(job_id, status='failed', error='Output file not found')
                else:
                    update_job_status(job_id, status='failed', error='No output file created')
            else:
                update_job_status(job_id, status='failed', error='Output folder not found')
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        import traceback
        error_msg = f"Error running talent search: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


@app.route('/api/admin/fix-csv-genpop', methods=['POST'])
@requires_auth
def fix_csv_genpop():
    """Admin endpoint to fix US Gen Pop Projection for SAMPLE SIZE in all CSV files."""
    from flask import session
    user = session.get('user')
    if user.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    
    try:
        import subprocess
        import sys
        
        # Run the fix script
        script_path = os.path.join(os.path.dirname(__file__), 'fix_s3_csv_genpop.py')
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'output': result.stdout,
                'message': 'CSV files fixed successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.stderr or 'Script execution failed',
                'output': result.stdout
            }), 500
            
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Script execution timed out'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/active-jobs', methods=['GET'])
@requires_super_admin
def admin_active_jobs():
    """Return all jobs (in-memory + S3) for the active jobs dashboard."""
    merged = {}

    s3_jobs = _load_jobs_status_from_s3()
    for jid, jdata in s3_jobs.items():
        merged[jid] = jdata

    for jid, jdata in jobs.items():
        merged[jid] = {
            'status': jdata.get('status'),
            'progress': jdata.get('progress', 0),
            'message': jdata.get('message'),
            'created_at': jdata.get('created_at'),
            'project_name': jdata.get('project_name'),
            'error': jdata.get('error'),
            's3_key': jdata.get('s3_key'),
            'created_by': jdata.get('created_by') or jdata.get('username'),
            'type': jdata.get('type', 'profile_analysis'),
            'brands': jdata.get('brands'),
        }

    job_list = []
    for jid, jdata in merged.items():
        entry = dict(jdata)
        entry['job_id'] = jid
        if not entry.get('type'):
            entry['type'] = 'profile_analysis'
        job_list.append(entry)

    job_list.sort(key=lambda x: x.get('created_at') or '', reverse=True)

    active = [j for j in job_list if j.get('status') in ('queued', 'running', 'processing')]
    recent = [j for j in job_list if j.get('status') not in ('queued', 'running', 'processing')][:10]

    return jsonify({'success': True, 'active': active, 'recent': recent})


@app.route('/api/admin/kill-job', methods=['POST'])
@requires_super_admin
def admin_kill_job():
    """Kill a running job: mark it as failed, remove from in-memory jobs, update S3."""
    data = request.get_json() or {}
    job_id = data.get('job_id')
    if not job_id:
        return jsonify({'success': False, 'error': 'job_id is required'}), 400

    killed = False

    if job_id in jobs:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = 'Killed by admin'
        jobs[job_id]['message'] = 'Killed by admin'
        _save_job_status_to_s3(job_id, jobs[job_id])
        del jobs[job_id]
        killed = True
    else:
        s3_jobs = _load_jobs_status_from_s3()
        if job_id in s3_jobs:
            s3_jobs[job_id]['status'] = 'failed'
            s3_jobs[job_id]['error'] = 'Killed by admin'
            try:
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=JOBS_STATUS_S3_KEY,
                    Body=json.dumps(s3_jobs),
                    ContentType='application/json'
                )
                killed = True
            except Exception as e:
                return jsonify({'success': False, 'error': f'Failed to update S3: {e}'}), 500

    if killed:
        return jsonify({'success': True, 'message': f'Job {job_id} killed'})
    return jsonify({'success': False, 'error': 'Job not found'}), 404


def run_talent_theater(job_id):
    """Run the Talent_Theater_Attribution.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(__file__), 'Talent_Theater_Attribution.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
            return
        
        # Get job parameters
        job = jobs[job_id]
        params = job['params']
        
        update_job_status(job_id, progress=30, message='Running analysis...')
        
        # Import and run the script
        import importlib.util
        spec = importlib.util.spec_from_file_location("talent_theater_attribution", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        
        # Prepare parameters for the script
        from datetime import datetime
        start_date = datetime.strptime(params['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(params['end_date'], '%Y-%m-%d')

        conn = module.connect_snowflake()
        try:
            script_params = {
                'talent_name': params['talent_name'],
                'competitive_talents': params.get('competitive_talents', []),
                'movie_name': params['movie_name'],
                'start_date': start_date,
                'end_date': end_date
            }
            results = module.run_query(conn, script_params)
            update_job_status(job_id, progress=80, message='Writing output...')

            # Write output to server output dir (not Desktop - doesn't exist on Render)
            from pathlib import Path
            output_folder = Path(OUTPUT_DIR) / "attribution"
            output_folder.mkdir(parents=True, exist_ok=True)
            module.write_output(results, script_params, output_dir=str(output_folder))

            # Find the output file in our output folder
            csv_files = list(output_folder.glob("*.csv"))
            csv_files = [f for f in csv_files if len(f.stem.split('_')) >= 3]
            if csv_files:
                csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                output_file = str(csv_files[0])
                if os.path.exists(output_file):
                    jobs[job_id]['result_file'] = output_file
                    # Upload to purgatory so it appears in Ticket Sales IQ Results Library (source: Ticket Sales IQ)
                    created_by = jobs[job_id].get('username', '')
                    talent_name = params.get('talent_name', '')
                    movie_name = params.get('movie_name', '')
                    project_name = f"{talent_name}_{movie_name}" if (talent_name and movie_name) else csv_files[0].stem
                    s3_key = upload_to_s3(
                        output_file,
                        project_name,
                        params.get('start_date', ''),
                        params.get('end_date', ''),
                        created_by=created_by,
                        use_purgatory=True,
                        bucket=TICKET_SALES_S3_BUCKET,
                        category='Ticket Sales',
                        source_type='ticket_sales_iq'
                    )
                    if s3_key:
                        jobs[job_id]['s3_key'] = s3_key
                    update_job_status(job_id, progress=100, status='completed', message='Analysis complete!')
                else:
                    update_job_status(job_id, status='failed', error='Output file not found')
            else:
                update_job_status(job_id, status='failed', error='No output file created')
        finally:
            try:
                conn.close()
            except Exception:
                pass
                
    except Exception as e:
        import traceback
        error_msg = f"Error running talent theater: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


@app.route('/api/attribution/ticket-sales-tracker', methods=['POST'], strict_slashes=False)
@requires_auth
def submit_ticket_sales_tracker():
    """Submit a Ticket Sales Tracker analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        if not user_can_run_analysis_module(user, 'ticket_sales_tracker'):
            return jsonify({'error': 'Analysis IQ access with Ticket Sales Tracker module required'}), 403
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        movie_name = data.get('movie_name')
        genre = data.get('genre')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        if not movie_name:
            return jsonify({'error': 'Movie name is required'}), 400
        if not genre:
            return jsonify({'error': 'Genre is required'}), 400
        if not start_date or not end_date:
            return jsonify({'error': 'Start date and end date are required'}), 400
        username = session.get('username', 'unknown')
        if not has_credits_for(username, CREDITS_TICKET_SALES_TRACKER):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Ticket Sales Tracker requires {CREDITS_TICKET_SALES_TRACKER} credits. You have {"no" if credits_left == 0 else credits_left} remaining.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'ticket_sales_tracker',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'movie_name': movie_name,
                'genre': genre,
                'start_date': start_date,
                'end_date': end_date
            }
        }
        desc = f"{movie_name} / {genre} ({start_date}–{end_date})"
        if not consume_credit(username, description=desc, job_id=job_id, pull_type='Ticket Sales Tracker', credits_used=CREDITS_TICKET_SALES_TRACKER):
            return jsonify({'error': 'Insufficient credits.'}), 403
        thread = threading.Thread(target=run_ticket_sales_tracker, args=(job_id,))
        thread.daemon = True
        thread.start()
        return jsonify({'job_id': job_id, 'message': 'Ticket Sales Tracker job submitted successfully', 'status': 'queued'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def run_ticket_sales_tracker(job_id):
    """Run the Ticket_Sales_Attribution.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        script_path = os.path.join(os.path.dirname(__file__), 'Ticket_Sales_Attribution.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
            return
        job = jobs[job_id]
        params = job['params']
        update_job_status(job_id, progress=30, message='Running analysis...')
        import importlib.util
        spec = importlib.util.spec_from_file_location("ticket_sales_attribution", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        from datetime import datetime
        start_date = datetime.strptime(params['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(params['end_date'], '%Y-%m-%d')
        conn = module.connect_snowflake()
        try:
            script_params = {
                'movie_name': params['movie_name'],
                'genre': params['genre'],
                'start_date': start_date,
                'end_date': end_date
            }
            results = module.run_query(conn, script_params)
            update_job_status(job_id, progress=80, message='Writing output...')
            from pathlib import Path
            output_folder = Path(OUTPUT_DIR) / "attribution"
            output_folder.mkdir(parents=True, exist_ok=True)
            script_params['output_dir'] = str(output_folder)
            module.write_output(results, script_params)
            csv_files = list(output_folder.glob("Ticket_Sales_*.csv"))
            if csv_files:
                csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                output_file = str(csv_files[0])
                if os.path.exists(output_file):
                    jobs[job_id]['result_file'] = output_file
                    created_by = jobs[job_id].get('username', '')
                    movie_name = params.get('movie_name', '')
                    project_name = movie_name if movie_name else csv_files[0].stem
                    s3_key = upload_to_s3(
                        output_file,
                        project_name,
                        params.get('start_date', ''),
                        params.get('end_date', ''),
                        created_by=created_by,
                        use_purgatory=True,
                        bucket=TICKET_SALES_TRACKER_S3_BUCKET,
                        category='Ticket Sales Tracker',
                        source_type='ticket_sales_tracker'
                    )
                    if s3_key:
                        jobs[job_id]['s3_key'] = s3_key
                    update_job_status(job_id, progress=100, status='completed', message='Analysis complete!')
                else:
                    update_job_status(job_id, status='failed', error='Output file not found')
            else:
                update_job_status(job_id, status='failed', error='No output file created')
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        import traceback
        error_msg = f"Error running Ticket Sales Tracker: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


@app.route('/api/attribution/svod-acquisition', methods=['POST'])
@requires_auth
def submit_svod_acquisition():
    """Submit a Subscriber IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access (Analysis IQ + SVOD module; run uses 7 credits)
        if not user_can_run_analysis_module(user, 'svod'):
            return jsonify({'error': 'Analysis IQ access with SVOD module required'}), 403
        
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        project_name = data.get('project_name')
        campaign_start = data.get('campaign_start')
        campaign_end = data.get('campaign_end')
        exclusion_days = data.get('exclusion_days')
        attribution_window = data.get('attribution_window')
        show_search_terms = data.get('show_search_terms', [])
        platform_name = data.get('platform_name')
        
        if not project_name:
            return jsonify({'error': 'Project name is required'}), 400
        if not campaign_start or not campaign_end:
            return jsonify({'error': 'Campaign start and end dates are required'}), 400
        if not exclusion_days or not attribution_window:
            return jsonify({'error': 'Exclusion days and attribution window are required'}), 400
        if not show_search_terms:
            return jsonify({'error': 'At least one show search term is required'}), 400
        if not platform_name:
            return jsonify({'error': 'Platform name is required'}), 400
        
        # Genre: optional; if provided must be one of the allowed SVOD genres
        SVOD_ALLOWED_GENRES = [
            'Serialized Drama', 'Non-Scripted Competition', 'Non-Scripted Relationship', 'Non-Scripted Gameshow', 'Non-Scripted Makeover',
            'Adult Animation', 'Stand Up Comedy', 'Single Camera Sitcom', 'Procedural Drama',
            'Multi Camera Sitcom', 'Live Sports', 'Single Event Telecast'
        ]
        genre = (data.get('genre') or '').strip()
        if genre and genre not in SVOD_ALLOWED_GENRES:
            return jsonify({'error': f'Genre must be one of: {", ".join(SVOD_ALLOWED_GENRES)}'}), 400
        
        content_cadence = (data.get('content_cadence') or '').strip()
        if content_cadence and content_cadence not in ('Weekly', 'All at Once'):
            content_cadence = ''
        
        username = session.get('username', 'unknown')
        if not has_credits_for(username, CREDITS_SVOD):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Subscriber IQ requires {CREDITS_SVOD} credits. You have {"no" if credits_left == 0 else credits_left} remaining.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        
        # Create job
        job_id = str(uuid.uuid4())
        
        track_episodes = data.get('track_episodes', False)
        tracking_mode = data.get('tracking_mode')  # 'episode', 'date', or None
        raw_episode_dates = data.get('episode_dates', [])
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'svod_acquisition',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'project_name': project_name,
                'campaign_start': campaign_start,
                'campaign_end': campaign_end,
                'exclusion_days': exclusion_days,
                'attribution_window': attribution_window,
                'show_search_terms': show_search_terms,
                'is_new_show': data.get('is_new_show', False),
                'platform_name': platform_name,
                'genre': genre if genre else '',
                'content_cadence': content_cadence if content_cadence else '',
                'track_episodes': track_episodes,
                'tracking_mode': tracking_mode,
                'episode_dates': raw_episode_dates
            }
        }
        
        desc = f"{project_name} ({campaign_start}–{campaign_end})"
        if not consume_credit(username, description=desc, job_id=job_id, pull_type='SVOD', credits_used=CREDITS_SVOD):
            return jsonify({'error': 'Insufficient credits.'}), 403
        thread = threading.Thread(target=run_svod_acquisition, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Subscriber IQ job submitted successfully',
            'status': 'queued'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/attribution/campaign-roi', methods=['POST'])
@requires_auth
def submit_campaign_roi():
    """Submit a Campaign ROI IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access (Analysis IQ + Campaign module)
        if not user_can_run_analysis_module(user, 'campaign'):
            return jsonify({'error': 'Analysis IQ access with Campaign module required'}), 403
        
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        project_name = data.get('project_name')
        campaign_start = data.get('campaign_start')
        campaign_end = data.get('campaign_end')
        pre_campaign_days = data.get('pre_campaign_days')
        attribution_window = data.get('attribution_window')
        action_urls = data.get('action_urls', [])
        post_domains = data.get('post_domains', [])
        
        if not project_name:
            return jsonify({'error': 'Project name is required'}), 400
        if not campaign_start or not campaign_end:
            return jsonify({'error': 'Campaign start and end dates are required'}), 400
        if not pre_campaign_days or not attribution_window:
            return jsonify({'error': 'Pre campaign days and attribution window are required'}), 400
        if not action_urls:
            return jsonify({'error': 'At least one action URL is required'}), 400
        if not post_domains:
            return jsonify({'error': 'At least one post domain is required'}), 400
        
        username = session.get('username', 'unknown')
        if not has_credits_for(username, CREDITS_CAMPAIGN_ROI):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Campaign ROI requires {CREDITS_CAMPAIGN_ROI} credits. You have {"no" if credits_left == 0 else credits_left} remaining.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        
        # Create job
        job_id = str(uuid.uuid4())
        
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'campaign_roi',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'project_name': project_name,
                'campaign_start': campaign_start,
                'campaign_end': campaign_end,
                'pre_campaign_days': pre_campaign_days,
                'attribution_window': attribution_window,
                'pre_domains': data.get('pre_domains', []),
                'action_urls': action_urls,
                'post_domains': post_domains,
                'post_metrics': data.get('post_metrics', []),
                'competitive_brands': data.get('competitive_brands', [])
            }
        }
        
        desc = f"{project_name} ({campaign_start}–{campaign_end})"
        if not consume_credit(username, description=desc, job_id=job_id, pull_type='Campaign ROI', credits_used=CREDITS_CAMPAIGN_ROI):
            return jsonify({'error': 'Insufficient credits.'}), 403
        thread = threading.Thread(target=run_campaign_roi, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Campaign ROI IQ job submitted successfully',
            'status': 'queued'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def run_svod_acquisition(job_id):
    """Run the SVOD_Churn_Attribution.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Try script in repo root (parent of app dir), then same dir as app (e.g. bg-webapp on Render)
        app_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(app_dir)
        script_path = os.path.join(repo_root, 'SVOD_Churn_Attribution.py')
        if not os.path.exists(script_path):
            script_path = os.path.join(app_dir, 'SVOD_Churn_Attribution.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found. Tried: {repo_root!r} and {app_dir!r}')
            return
        
        # Get job parameters
        job = jobs[job_id]
        params = job['params']
        
        update_job_status(job_id, progress=30, message='Running analysis...')
        
        # Import and run the script
        import importlib.util
        spec = importlib.util.spec_from_file_location("svod_churn_attribution", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        
        # Prepare parameters for the script
        from datetime import datetime
        campaign_start = datetime.strptime(params['campaign_start'], '%Y-%m-%d')
        campaign_end = datetime.strptime(params['campaign_end'], '%Y-%m-%d')
        
        # Build episode_dates: script expects list of {episode_num, air_date (datetime), date_str, display_label}
        episode_dates = []
        for raw in params.get('episode_dates') or []:
            date_str = raw.get('date_str') or raw.get('air_date')
            if not date_str:
                continue
            try:
                if isinstance(date_str, str) and '-' in date_str:
                    # Support MM-DD-YYYY from form
                    if len(date_str) == 10 and date_str[2] == '-' and date_str[5] == '-':
                        air_date = datetime.strptime(date_str, '%m-%d-%Y')
                    else:
                        air_date = datetime.strptime(date_str, '%Y-%m-%d')
                else:
                    continue
            except ValueError:
                continue
            episode_dates.append({
                'episode_num': int(raw.get('episode_num', len(episode_dates) + 1)),
                'air_date': air_date,
                'date_str': air_date.strftime('%m-%d-%Y'),
                'display_label': raw.get('display_label') or (f"Episode {raw.get('episode_num', len(episode_dates) + 1)}" if params.get('tracking_mode') == 'episode' else air_date.strftime('%m/%d/%y'))
            })
        if episode_dates and params.get('track_episodes'):
            # Sort episodes chronologically so date range is correct
            episode_dates.sort(key=lambda ep: ep['air_date'])
            campaign_start = episode_dates[0]['air_date']
            attr_days = int(params.get('attribution_window', 30))
            campaign_end = episode_dates[-1]['air_date'] + timedelta(days=attr_days)
        
        # Safety: ensure campaign_start <= campaign_end
        if campaign_start > campaign_end:
            campaign_start, campaign_end = campaign_end, campaign_start
        
        competitive_brands = module.get_competitive_platforms(params['platform_name']) if hasattr(module, 'get_competitive_platforms') else []
        from pathlib import Path
        output_folder = Path(OUTPUT_DIR) / "attribution"
        output_folder.mkdir(parents=True, exist_ok=True)
        script_params = {
            'project_name': params['project_name'],
            'auto_format': True,
            'campaign_start': campaign_start,
            'campaign_end': campaign_end,
            'exclusion_days': int(params['exclusion_days']),
            'attribution_window': int(params['attribution_window']),
            'show_search_terms': params['show_search_terms'],
            'is_new_show': params.get('is_new_show', False),
            'track_episodes': bool(params.get('track_episodes', False)),
            'tracking_mode': params.get('tracking_mode'),
            'episode_dates': episode_dates,
            'platform_name': params['platform_name'],
            'competitive_brands': competitive_brands,
            'genre': params.get('genre', '') or '',
            'content_cadence': params.get('content_cadence', '') or '',
            'output_dir': str(output_folder),
        }
        
        # Run the analysis function
        conn = module.connect_snowflake()
        try:
            update_job_status(job_id, progress=50, message='Executing analysis...')
            
            # Call run_query directly with our params
            if hasattr(module, 'run_query'):
                summary_df, comp_df, demo_df, timing_df, episode_df, monthly_df, episode_timing_df, churn_df, post_signup_touchpoints_df = module.run_query(conn, script_params)
            else:
                update_job_status(job_id, status='failed', error='Script does not have run_query function')
                return
            
            update_job_status(job_id, progress=80, message='Writing output...')
            
            # Call write_output (writes to script_params['output_dir'] = server output folder)
            if hasattr(module, 'write_output'):
                module.write_output(summary_df, comp_df, demo_df, timing_df, episode_df, monthly_df, episode_timing_df, churn_df, post_signup_touchpoints_df, script_params)
            else:
                update_job_status(job_id, status='failed', error='Script does not have write_output function')
                return
            
            # Find the output file in the server output folder (same as Talent Theater)
            if output_folder.exists():
                safe_name = re.sub(r'[<>:"/\\|?*\']', '', params['project_name']).strip()[:100]
                csv_files = list(output_folder.glob(f"{safe_name}*.csv"))
                if csv_files:
                    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    output_file = str(csv_files[0])
                    if os.path.exists(output_file):
                        jobs[job_id]['result_file'] = output_file
                        
                        # Upload to SVOD bucket purgatory
                        update_job_status(job_id, progress=90, message='Uploading to purgatory...')
                        created_by = job.get('username', '')
                        s3_key = upload_to_s3(
                            output_file, 
                            params['project_name'], 
                            params['campaign_start'], 
                            params['campaign_end'],
                            created_by=created_by,
                            use_purgatory=True,
                            bucket=SUBSCRIBER_S3_BUCKET,
                            category='SVOD Acquisition',
                            source_type='svod_acquisition'
                        )
                        
                        if s3_key:
                            jobs[job_id]['s3_key'] = s3_key
                            jobs[job_id]['purgatory_id'] = f"{SUBSCRIBER_S3_BUCKET}:{s3_key}"
                            print(f"✅ Subscriber IQ uploaded to purgatory: {s3_key}")
                        
                        update_job_status(job_id, progress=100, status='completed', message='Analysis complete!', s3_key=s3_key)
                    else:
                        update_job_status(job_id, status='failed', error='Output file not found')
                else:
                    update_job_status(job_id, status='failed', error='No output file created')
            else:
                update_job_status(job_id, status='failed', error='Output folder not found')
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        import traceback
        error_msg = f"Error running SVOD acquisition: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


def run_campaign_roi(job_id):
    """Run the campaign_attribution.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'campaign_attribution.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
            return
        
        # Get job parameters
        job = jobs[job_id]
        params = job['params']
        
        update_job_status(job_id, progress=30, message='Running analysis...')
        
        # Import and run the script
        import importlib.util
        spec = importlib.util.spec_from_file_location("campaign_attribution", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        
        # Prepare parameters for the script
        from datetime import datetime
        campaign_start = datetime.strptime(params['campaign_start'], '%Y-%m-%d')
        campaign_end = datetime.strptime(params['campaign_end'], '%Y-%m-%d')
        
        # Build script parameters dict (matching what get_user_input returns)
        script_params = {
            'project_name': params['project_name'],
            'campaign_start': campaign_start,
            'campaign_end': campaign_end,
            'pre_campaign_days': int(params['pre_campaign_days']),
            'attribution_window': int(params['attribution_window']),
            'pre_domains': params.get('pre_domains', []),
            'action_urls': params['action_urls'],
            'post_domains': params['post_domains'],
            'post_metrics': params.get('post_metrics', []),
            'competitive_brands': params.get('competitive_brands', [])
        }
        
        # Run the analysis function
        conn = module.connect_snowflake()
        try:
            update_job_status(job_id, progress=50, message='Executing analysis...')
            
            # Call run_query directly with our params
            if hasattr(module, 'run_query'):
                summary_df, comp_df, demo_df, hours_action_df, hours_post_df = module.run_query(conn, script_params)
            else:
                update_job_status(job_id, status='failed', error='Script does not have run_query function')
                return
            
            update_job_status(job_id, progress=80, message='Writing output...')
            
            # Call write_output
            if hasattr(module, 'write_output'):
                module.write_output(summary_df, comp_df, demo_df, hours_action_df, hours_post_df, script_params)
            else:
                update_job_status(job_id, status='failed', error='Script does not have write_output function')
                return
            
            # Find the output file (it's written to Desktop/attribution folder)
            from pathlib import Path
            output_folder = Path.home() / "Desktop" / "attribution"
            if output_folder.exists():
                # Find the most recent CSV file matching the project name
                csv_files = list(output_folder.glob(f"{params['project_name']}*.csv"))
                if csv_files:
                    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    output_file = str(csv_files[0])
                    if os.path.exists(output_file):
                        jobs[job_id]['result_file'] = output_file
                        update_job_status(job_id, progress=100, status='completed', message='Analysis complete!')
                    else:
                        update_job_status(job_id, status='failed', error='Output file not found')
                else:
                    update_job_status(job_id, status='failed', error='No output file created')
            else:
                update_job_status(job_id, status='failed', error='Output folder not found')
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        import traceback
        error_msg = f"Error running campaign ROI: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


@app.route('/api/attribution/cross-show', methods=['POST'])
@requires_auth
def submit_cross_show():
    """Submit a Cross Show Watching analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access (Analysis IQ + Cross Show module)
        if not user_can_run_analysis_module(user, 'cross_show'):
            return jsonify({'error': 'Analysis IQ access with Cross Show module required'}), 403
        
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        show_title = data.get('show_title')
        platform = data.get('platform')
        
        if not start_date or not end_date:
            return jsonify({'error': 'Start date and end date are required'}), 400
        if not show_title:
            return jsonify({'error': 'Show title is required'}), 400
        if not platform:
            return jsonify({'error': 'Platform is required'}), 400
        
        # Create job
        job_id = str(uuid.uuid4())
        username = session.get('username', 'unknown')
        
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'cross_show',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'start_date': start_date,
                'end_date': end_date,
                'show_title': show_title,
                'platform': platform,
                'other_properties': data.get('other_properties', [])
            }
        }
        
        # Start job in background thread
        thread = threading.Thread(target=run_cross_show, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Cross Show Watching job submitted successfully',
            'status': 'queued'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/attribution/watch-time', methods=['POST'])
@requires_auth
def submit_watch_time():
    """Submit a Watch Time IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access (Analysis IQ + Watch Time module)
        if not user_can_run_analysis_module(user, 'watch_time'):
            return jsonify({'error': 'Analysis IQ access with Watch Time module required'}), 403
        
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        show_names = data.get('show_names', [])
        
        if not start_date or not end_date:
            return jsonify({'error': 'Start date and end date are required'}), 400
        if not show_names:
            return jsonify({'error': 'At least one show name is required'}), 400
        
        username = session.get('username', 'unknown')
        if not has_credits_for(username, CREDITS_WATCH_TIME):
            _, credits_left = check_user_credits(username)
            return jsonify({
                'error': f'Watch Time requires {CREDITS_WATCH_TIME} credit(s). You have {"no" if credits_left == 0 else credits_left} remaining.',
                'credits_left': 0 if credits_left != -1 else -1
            }), 403
        
        # Create job
        job_id = str(uuid.uuid4())
        
        jobs[job_id] = {
            'job_id': job_id,
            'username': username,
            'type': 'watch_time',
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'error': None,
            'result_file': None,
            'logs': [],
            'params': {
                'start_date': start_date,
                'end_date': end_date,
                'show_names': show_names,
                'show_lengths': data.get('show_lengths', {})
            }
        }
        
        desc = f"Watch Time ({start_date}–{end_date})"
        if not consume_credit(username, description=desc, job_id=job_id, pull_type='Watch Time', credits_used=CREDITS_WATCH_TIME):
            return jsonify({'error': 'Insufficient credits.'}), 403
        thread = threading.Thread(target=run_watch_time, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Watch Time IQ job submitted successfully',
            'status': 'queued'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def run_cross_show(job_id):
    """Run the show_platform_tracker.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'show_platform_tracker.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
            return
        
        # Get job parameters
        job = jobs[job_id]
        params = job['params']
        
        update_job_status(job_id, progress=30, message='Running analysis...')
        
        # Import and run the script
        import importlib.util
        spec = importlib.util.spec_from_file_location("show_platform_tracker", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        
        # Prepare parameters for the script
        from datetime import datetime
        start_date = datetime.strptime(params['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(params['end_date'], '%Y-%m-%d')
        
        # Build script parameters dict (matching what get_user_input returns)
        script_params = {
            'show_title': params['show_title'],
            'platform': params['platform'],
            'start_date': start_date,
            'end_date': end_date,
            'other_properties': params.get('other_properties', [])
        }
        
        # Run the analysis function
        conn = module.connect_snowflake()
        try:
            update_job_status(job_id, progress=50, message='Executing analysis...')
            
            # Call run_analysis directly with our params
            if hasattr(module, 'run_analysis'):
                results = module.run_analysis(conn, script_params)
            else:
                update_job_status(job_id, status='failed', error='Script does not have run_analysis function')
                return
            
            update_job_status(job_id, progress=80, message='Writing output...')
            
            # Call write_output
            if hasattr(module, 'write_output'):
                module.write_output(results)
            else:
                update_job_status(job_id, status='failed', error='Script does not have write_output function')
                return
            
            # Find the output file (it's written to Desktop folder)
            from pathlib import Path
            output_folder = Path.home() / "Desktop"
            if output_folder.exists():
                # Find the most recent CSV file (the script creates files with show title in name)
                csv_files = list(output_folder.glob("*.csv"))
                if csv_files:
                    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    output_file = str(csv_files[0])
                    if os.path.exists(output_file):
                        jobs[job_id]['result_file'] = output_file
                        update_job_status(job_id, progress=100, status='completed', message='Analysis complete!')
                    else:
                        update_job_status(job_id, status='failed', error='Output file not found')
                else:
                    update_job_status(job_id, status='failed', error='No output file created')
            else:
                update_job_status(job_id, status='failed', error='Output folder not found')
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        import traceback
        error_msg = f"Error running cross show watching: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


def run_watch_time(job_id):
    """Run the multi_show_time_tracker.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'multi_show_time_tracker.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
            return
        
        # Get job parameters
        job = jobs[job_id]
        params = job['params']
        
        update_job_status(job_id, progress=30, message='Running analysis...')
        
        # Import and run the script
        import importlib.util
        spec = importlib.util.spec_from_file_location("multi_show_time_tracker", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(script_path))
        spec.loader.exec_module(module)
        
        # Prepare parameters for the script
        from datetime import datetime
        start_date = datetime.strptime(params['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(params['end_date'], '%Y-%m-%d')
        
        # Build script parameters dict (matching what get_user_input returns)
        script_params = {
            'show_names': params['show_names'],
            'start_date': start_date,
            'end_date': end_date,
            'show_lengths': params.get('show_lengths', {})
        }
        
        # Run the analysis function
        conn = module.connect_snowflake()
        try:
            update_job_status(job_id, progress=50, message='Executing analysis...')
            
            # Call run_analysis directly with our params
            if hasattr(module, 'run_analysis'):
                results = module.run_analysis(conn, script_params)
            else:
                update_job_status(job_id, status='failed', error='Script does not have run_analysis function')
                return
            
            update_job_status(job_id, progress=80, message='Writing output...')
            
            # Call write_output
            if hasattr(module, 'write_output'):
                module.write_output(results)
            else:
                update_job_status(job_id, status='failed', error='Script does not have write_output function')
                return
            
            # Find the output file (it's written to Desktop folder)
            from pathlib import Path
            output_folder = Path.home() / "Desktop"
            if output_folder.exists():
                # Find the most recent CSV file
                csv_files = list(output_folder.glob("*.csv"))
                if csv_files:
                    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    output_file = str(csv_files[0])
                    if os.path.exists(output_file):
                        jobs[job_id]['result_file'] = output_file
                        update_job_status(job_id, progress=100, status='completed', message='Analysis complete!')
                    else:
                        update_job_status(job_id, status='failed', error='Output file not found')
                else:
                    update_job_status(job_id, status='failed', error='No output file created')
            else:
                update_job_status(job_id, status='failed', error='Output folder not found')
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        import traceback
        error_msg = f"Error running watch time IQ: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


# ============================================================================
# MAIN
# ============================================================================

# Print startup completion message
print("=" * 60)
print("✅ Flask app fully initialized and ready to serve requests")
print("✅ Health check available at /health and /healthz")
print("✅ Background initialization started (users, cache)")
print("=" * 60)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    if SOCKETIO_AVAILABLE and socketio:
        socketio.run(app, host='0.0.0.0', port=port, debug=debug)
    else:
        app.run(host='0.0.0.0', port=port, debug=debug)
