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
import csv
import threading
import traceback
import re
import io
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session
from flask_cors import CORS
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

# Add parent directory to path for importing bg module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

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

# ============================================================================
# CONFIGURATION
# ============================================================================

S3_BUCKET = 'dashboard-inputs'
SUBSCRIBER_S3_BUCKET = 'svod-acquisition'  # Bucket for Subscriber IQ data
HEDGE_FUND_S3_BUCKET = 'aggregated-tickers'  # Bucket for Hedge Fund IQ ticker data
# FORCE us-east-2 - all buckets are in this region, ignore AWS_REGION env var if set
S3_REGION = 'us-east-2'
USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

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
    s3_client = boto3.client(
        's3',
        region_name=S3_REGION,
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        config=config
    )
    print(f"✅ S3 client initialized for region: {S3_REGION} with signature version 4")
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
        s3_client = boto3.client(
            's3',
            region_name=S3_REGION,
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

def generate_ai_insights(profile_data):
    """Generate AI-powered insights from profile data."""
    client = get_openai_client()
    if not client:
        return {"error": "OpenAI not configured. Add OPENAI_API_KEY to environment variables."}
    
    try:
        # Prepare data summary for GPT
        demographics = profile_data.get('demographics', {})
        behavioral = profile_data.get('behavioral', {})
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'This audience')
        
        # Build context
        demo_summary = []
        for cat, values in demographics.items():
            if isinstance(values, dict):
                top_items = sorted(values.items(), key=lambda x: x[1], reverse=True)[:3]
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
                behavior_summary.append(f"{cat}: {', '.join(item_strs)}")
        
        prompt = f"""Analyze this audience profile and provide 5 key insights in bullet points. Be specific and actionable.

Profile: {profile_name}
Sample Size: {sample_size:,}

Demographics:
{chr(10).join(demo_summary[:8])}

Top Behaviors:
{chr(10).join(behavior_summary[:10])}

Provide insights about:
1. Who this audience is (demographics)
2. What makes them unique vs general population
3. Their media consumption habits
4. Potential marketing opportunities
5. Key differentiators

Keep each insight to 1-2 sentences. Be specific with numbers."""

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
        demographics = profile_data.get('demographics', {})
        behavioral = profile_data.get('behavioral', {})
        profile_name = profile_data.get('name', 'Audience')
        
        # Get top demographics
        gender = demographics.get('gender', {})
        age = demographics.get('age', {})
        income = demographics.get('income', {})
        
        top_gender = max(gender.items(), key=lambda x: x[1])[0] if gender else "Unknown"
        top_age = max(age.items(), key=lambda x: x[1])[0] if age else "Unknown"
        top_income = max(income.items(), key=lambda x: x[1])[0] if income else "Unknown"
        
        # Get top behaviors
        top_behaviors = []
        for cat, items in behavioral.items():
            if isinstance(items, list):
                for item in items[:2]:
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
        demographics = profile_data.get('demographics', {})
        behavioral = profile_data.get('behavioral', {})
        profile_name = profile_data.get('name', 'Audience')
        
        # Compile behavioral insights
        behavior_list = []
        for cat, items in behavioral.items():
            if isinstance(items, list):
                for item in items[:3]:
                    behavior_list.append(f"{item.get('name', '')} ({item.get('pct', 0):.1f}%)")
        
        prompt = f"""Create a comprehensive marketing strategy for reaching this audience.

Profile: {profile_name}
Demographics: {json.dumps(demographics, default=str)[:500]}
Key Behaviors: {', '.join(behavior_list[:15])}

Provide:
1. **Channel Strategy** - Which platforms/channels to prioritize and why
2. **Content Strategy** - Types of content that will resonate
3. **Messaging Framework** - Key themes and tone to use
4. **Campaign Ideas** - 3 specific campaign concepts
5. **Timing Recommendations** - Best times/days to reach them
6. **Budget Allocation** - Suggested % split across channels

Be specific and actionable. Reference the actual data provided."""

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
        demographics = profile_data.get('demographics', {})
        behavioral = profile_data.get('behavioral', {})
        locations = profile_data.get('locations', [])
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'This audience')
        
        # Build comprehensive data context
        data_context = f"""
Profile: {profile_name}
Sample Size: {sample_size:,}

DEMOGRAPHICS:
{json.dumps(demographics, indent=2, default=str)[:1500]}

TOP BEHAVIORS BY CATEGORY:
{json.dumps({k: v[:5] if isinstance(v, list) else v for k, v in list(behavioral.items())[:10]}, indent=2, default=str)[:2000]}

TOP LOCATIONS:
{json.dumps(locations[:10], indent=2, default=str)[:500]}
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"You are an audience data analyst. Answer questions about this profile data accurately and concisely. Always cite specific numbers from the data when relevant.\n\nDATA:\n{data_context}"},
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
        demographics = profile_data.get('demographics', {})
        behavioral = profile_data.get('behavioral', {})
        locations = profile_data.get('locations', [])
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'This audience')
        
        # Build data context
        behavior_summary = []
        for cat, items in behavioral.items():
            if isinstance(items, list) and items:
                top_items = items[:5]
                item_strs = []
                for i in top_items:
                    name = i.get('name', '')
                    pct = i.get('pct', 0)
                    item_strs.append(f"{name} ({pct:.1f}%)")
                behavior_summary.append(f"{cat}: {', '.join(item_strs)}")
        
        data_context = f"""
AUDIENCE PROFILE: {profile_name}
Sample Size: {sample_size:,}

DEMOGRAPHICS:
{json.dumps(demographics, indent=2, default=str)[:1500]}

KEY BEHAVIORS:
{chr(10).join(behavior_summary[:15])}

TOP LOCATIONS:
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

AUDIENCE DATA:
{data_context}

Always format your response with:
1. **Direct Answer** - Address their question with data-backed insights
2. **Key Data Points** - Bullet the most relevant numbers
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
        demographics = profile_data.get('demographics', {})
        behavioral = profile_data.get('behavioral', {})
        sample_size = profile_data.get('sampleSize', 0)
        profile_name = profile_data.get('name', 'Audience')
        
        # Build data summary
        behavior_summary = []
        for cat, items in behavioral.items():
            if isinstance(items, list) and items:
                behavior_summary.append(f"{cat}: {', '.join([i.get('name', '') for i in items[:3]])}")
        
        prompt = f"""Create a professional presentation deck outline to answer this business question:

BUSINESS QUESTION: {business_question}

AUDIENCE: {profile_name}
Sample Size: {sample_size:,}

KEY DEMOGRAPHICS:
- Gender: {json.dumps(demographics.get('gender', {}), default=str)}
- Age: {json.dumps(demographics.get('age', {}), default=str)}
- Income: {json.dumps(demographics.get('income', {}), default=str)}

TOP BEHAVIORS:
{chr(10).join(behavior_summary[:10])}

{f'PREVIOUS FINDINGS: {key_findings}' if key_findings else ''}

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
            demo = profile.get('demographics', {})
            behaviors = profile.get('behavioral', {})
            
            # Get key stats
            top_behaviors = []
            for cat, items in behaviors.items():
                if isinstance(items, list) and items:
                    top_behaviors.append(f"{items[0].get('name', '')} ({cat})")
            
            profiles_summary.append(f"""
{name}:
- Gender: {json.dumps(demo.get('gender', {}), default=str)[:200]}
- Age: {json.dumps(demo.get('age', {}), default=str)[:200]}
- Top Behaviors: {', '.join(top_behaviors[:5])}
""")
        
        prompt = f"""Compare these audience profiles and identify:
1. Key similarities between the audiences
2. Key differences that set each apart
3. Overlap opportunities (where they might be reached together)
4. Distinct positioning for each

Profiles:
{''.join(profiles_summary)}

Be specific with numbers and percentages."""

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
        if not user or user.get('role') != 'admin':
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
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
        session['role'] = user.get('role', 'user')
        
        # Always redirect to dashboard, admin can access admin panel from there
        return jsonify({'success': True, 'redirect': '/'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

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
        save_gmail_tokens({
            'access_token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'email': profile.get('emailAddress')
        })
        
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
Crosswalk Team
    """
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background: #16213e; border-radius: 12px; padding: 30px; }}
        h1 {{ color: #00d9ff; margin-bottom: 20px; }}
        .credentials {{ background: #0f3460; border-radius: 8px; padding: 20px; margin: 20px 0; }}
        .field {{ margin: 10px 0; }}
        .label {{ color: #888; font-size: 12px; text-transform: uppercase; }}
        .value {{ font-size: 18px; font-weight: bold; color: #fff; font-family: monospace; }}
        .btn {{ display: inline-block; background: linear-gradient(135deg, #00d9ff, #0099cc); color: #000; padding: 12px 30px; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 20px; }}
        .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎉 Welcome to Crosswalk!</h1>
        <p>Your account has been created. Here are your login details:</p>
        
        <div class="credentials">
            <div class="field">
                <div class="label">Username</div>
                <div class="value">{username}</div>
            </div>
            <div class="field">
                <div class="label">Password</div>
                <div class="value">{password}</div>
            </div>
            <div class="field">
                <div class="label">Role</div>
                <div class="value">{role.upper()}</div>
            </div>
        </div>
        
        <a href="{app_url}/login" class="btn">Login Now →</a>
        
        <p style="margin-top: 20px;">You can change your password after logging in if you'd like.</p>
        
        <div class="footer">
            <p>If you have any questions, please contact your administrator.</p>
            <p>— Crosswalk Team</p>
        </div>
    </div>
</body>
</html>
    """
    
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
        
        role = req_data.get('role', 'user')
        
        data['users'][username] = {
            'password_hash': hash_password(password),
            'email': email,
            'first_name': req_data.get('first_name', ''),
            'last_name': req_data.get('last_name', ''),
            'company': req_data.get('company', ''),
            'department': req_data.get('department', ''),
            'role': role,
            'credits': req_data.get('credits', 5),
            'credits_used': 0,
            'created_at': datetime.now().isoformat(),
            'last_login': None,
            'access_expires': req_data.get('access_expires'),  # None = unlimited
            'allowed_categories': req_data.get('allowed_categories', ['*']),
            'allowed_runs': req_data.get('allowed_runs', ['*']),
            'has_profile_iq_access': req_data.get('has_profile_iq_access', True),
            'has_subscriber_iq_access': req_data.get('has_subscriber_iq_access', False),
            'has_hedge_fund_iq_access': req_data.get('has_hedge_fund_iq_access', False),
            'hedge_fund_iq_tabs': req_data.get('hedge_fund_iq_tabs', []),
            'hedge_fund_iq_tickers': req_data.get('hedge_fund_iq_tickers', []),
            'has_analysis_iq_access': req_data.get('has_analysis_iq_access', False),
            'analysis_iq_modules': req_data.get('analysis_iq_modules', [])
        }
        
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
            user['role'] = req_data['role']
        if 'credits' in req_data:
            user['credits'] = req_data['credits']
        if 'access_expires' in req_data:
            user['access_expires'] = req_data['access_expires']
        if 'allowed_categories' in req_data:
            user['allowed_categories'] = req_data['allowed_categories']
        if 'allowed_runs' in req_data:
            user['allowed_runs'] = req_data['allowed_runs']
        if 'has_profile_iq_access' in req_data:
            user['has_profile_iq_access'] = req_data['has_profile_iq_access']
        if 'has_subscriber_iq_access' in req_data:
            user['has_subscriber_iq_access'] = req_data['has_subscriber_iq_access']
        if 'has_hedge_fund_iq_access' in req_data:
            user['has_hedge_fund_iq_access'] = req_data['has_hedge_fund_iq_access']
        if 'hedge_fund_iq_tabs' in req_data:
            user['hedge_fund_iq_tabs'] = req_data['hedge_fund_iq_tabs']
        if 'hedge_fund_iq_tickers' in req_data:
            user['hedge_fund_iq_tickers'] = req_data['hedge_fund_iq_tickers']
        if 'has_analysis_iq_access' in req_data:
            user['has_analysis_iq_access'] = req_data['has_analysis_iq_access']
        if 'analysis_iq_modules' in req_data:
            user['analysis_iq_modules'] = req_data['analysis_iq_modules']
        
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

— Crosswalk Team
    """
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background: #16213e; border-radius: 12px; padding: 30px; }}
        h1 {{ color: #f59e0b; margin-bottom: 20px; }}
        .credentials {{ background: #0f3460; border-radius: 8px; padding: 20px; margin: 20px 0; }}
        .field {{ margin: 10px 0; }}
        .label {{ color: #888; font-size: 12px; text-transform: uppercase; }}
        .value {{ font-size: 18px; font-weight: bold; color: #fff; font-family: monospace; }}
        .btn {{ display: inline-block; background: linear-gradient(135deg, #f59e0b, #d97706); color: #000; padding: 12px 30px; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 20px; }}
        .footer {{ margin-top: 30px; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 Password Reset</h1>
        <p>Your password has been reset by an administrator. Here are your new login details:</p>
        
        <div class="credentials">
            <div class="field">
                <div class="label">Username</div>
                <div class="value">{username}</div>
            </div>
            <div class="field">
                <div class="label">New Password</div>
                <div class="value">{password}</div>
            </div>
        </div>
        
        <a href="{app_url}/login" class="btn">Login Now →</a>
        
        <p style="margin-top: 20px;">You can change your password after logging in if you'd like.</p>
        
        <div class="footer">
            <p>— Crosswalk Team</p>
        </div>
    </div>
</body>
</html>
    """
    
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
            if data['users'][username].get('role') != 'admin':
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
    """Get list of users Gmail is shared with."""
    try:
        tokens = load_gmail_tokens()
        if not tokens:
            return jsonify({'success': True, 'shared_with': []})
        
        return jsonify({
            'success': True,
            'shared_with': tokens.get('shared_with', []),
            'owner': 'admin'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/profile-picture', methods=['POST'])
@requires_auth
def upload_profile_picture():
    """Upload profile picture for current user or specified user (admin only)."""
    try:
        current_user = session.get('username')
        current_role = session.get('role')
        
        # Get target username (defaults to current user)
        target_username = request.form.get('username', current_user)
        
        # Only admin can change other users' pictures
        if target_username != current_user and current_role != 'admin':
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        
        # Validate file type
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in allowed_extensions:
            return jsonify({'success': False, 'error': 'Invalid file type. Use PNG, JPG, GIF, or WebP'})
        
        # Read and encode as base64 (store in user data for simplicity)
        import base64
        file_data = file.read()
        
        # Limit file size to 500KB
        if len(file_data) > 500 * 1024:
            return jsonify({'success': False, 'error': 'File too large. Max 500KB'})
        
        # Convert to base64 data URL
        mime_types = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}
        mime_type = mime_types.get(ext, 'image/png')
        base64_data = base64.b64encode(file_data).decode('utf-8')
        data_url = f"data:{mime_type};base64,{base64_data}"
        
        # Save to user data
        data = load_users()
        if target_username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        data['users'][target_username]['profile_picture'] = data_url
        save_users(data)
        
        return jsonify({'success': True, 'profile_picture': data_url})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/profile-picture', methods=['DELETE'])
@requires_auth
def delete_profile_picture():
    """Remove profile picture for current user or specified user (admin only)."""
    try:
        current_user = session.get('username')
        current_role = session.get('role')
        
        target_username = request.args.get('username', current_user)
        
        if target_username != current_user and current_role != 'admin':
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
    
    # Check AWS credentials
    aws_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    if not aws_key or not aws_secret:
        print("❌ AWS credentials not configured")
        return jsonify({'success': False, 'error': 'AWS credentials not configured'})
    
    try:
        s3 = boto3.client('s3',
                          aws_access_key_id=aws_key,
                          aws_secret_access_key=aws_secret,
                          region_name=S3_REGION)
        
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
                    # Skip historic folder and non-CSV files
                    if key.startswith('historic/') or not key.endswith('.csv'):
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
                    if key in svod_metadata and svod_metadata[key].get('category'):
                        category = svod_metadata[key]['category']
                    
                    svod_files.append({
                        'key': f'svod-acquisition/{key}',  # Prefix to identify bucket
                        'filename': filename,
                        'project_name': show_name,
                        'category': category,
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
        
        # Load profile image cache to check for custom images
        if not profile_image_cache:
            load_profile_image_cache()
        
        # Add custom image info to each file
        for f in active_files:
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
        
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                          region_name=S3_REGION)
        
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
        
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                          region_name=S3_REGION)
        
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
        
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                          aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                          region_name=S3_REGION)
        
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
                'role': user.get('role', 'user'),
                'company': user.get('company', ''),
                'department': user.get('department', '')
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
        'role': user.get('role', 'user'),
        'company': user.get('company', ''),
        'department': user.get('department', ''),
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
    role = user.get('role', 'user') if user else 'user'
    # Admins always have access to all dashboards and modules
    if role == 'admin':
        has_profile_iq = True
        has_subscriber_iq = True
        has_hedge_fund_iq = True
        hedge_fund_iq_tickers = ['*']
        has_analysis_iq = True
        analysis_iq_modules = ['profile_analysis', 'talent_search', 'talent_theater', 'svod', 'campaign', 'cross_show', 'watch_time']
    else:
        has_profile_iq = user.get('has_profile_iq_access', True) if user else True  # Default True for backward compat
        has_subscriber_iq = user.get('has_subscriber_iq_access', False) if user else False
        has_hedge_fund_iq = user.get('has_hedge_fund_iq_access', False) if user else False
        hedge_fund_iq_tickers = user.get('hedge_fund_iq_tickers', ['*']) if user else ['*']
        has_analysis_iq = user.get('has_analysis_iq_access', False) if user else False
        analysis_iq_modules = user.get('analysis_iq_modules', []) if user else []
    
    # Get user info for credits request
    first_name = user.get('first_name', '') if user else ''
    last_name = user.get('last_name', '') if user else ''
    email = user.get('email', '') if user else ''
    
    return render_template('index.html', 
                           username=session.get('username'),
                           role=role,
                           credits=user.get('credits', 0) if user else 0,
                           credits_used=user.get('credits_used', 0) if user else 0,
                           profile_picture=user.get('profile_picture', '') if user else '',
                           has_profile_iq_access=has_profile_iq,
                           has_subscriber_iq_access=has_subscriber_iq,
                           has_hedge_fund_iq_access=has_hedge_fund_iq,
                           hedge_fund_iq_tickers=hedge_fund_iq_tickers,
                           has_analysis_iq_access=has_analysis_iq,
                           analysis_iq_modules=analysis_iq_modules,
                           first_name=first_name,
                           last_name=last_name,
                           user_email=email)


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
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #c8e600;">Credit Request from Crosswalk IQ</h2>
            <p><strong>{first_name} {last_name}</strong> would like to buy more credits.</p>
            <p><strong>Username:</strong> {username}</p>
            <p><strong>Email:</strong> {user_email}</p>
            <p><strong>Current Credits:</strong> {user.get('credits', 0)}</p>
            <p><strong>Credits Used:</strong> {user.get('credits_used', 0)}</p>
            <hr style="border: 1px solid #333;">
            <p style="color: #888; font-size: 12px;">This request was sent from the Crosswalk IQ dashboard.</p>
        </div>
        """
        
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

@app.route('/api/wiki-image/<path:name>')
def get_wiki_image(name):
    """Get profile image - only returns admin-uploaded custom images."""
    global profile_image_cache
    
    # Load cache if empty
    if not profile_image_cache:
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
        # Load caches
        if not profile_image_cache:
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
            
            for row in demo_results:
                gender, age, income, ethnicity, count = row
                if gender:
                    demographics['gender'][gender] = demographics['gender'].get(gender, 0) + count
                if age:
                    demographics['age'][age] = demographics['age'].get(age, 0) + count
                if income:
                    demographics['income'][income] = demographics['income'].get(income, 0) + count
                if ethnicity:
                    demographics['ethnicity'][ethnicity] = demographics['ethnicity'].get(ethnicity, 0) + count
            
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
        
        # Check for section headers - can be in first or second column
        first_col = row[0].strip() if len(row) > 0 and row[0] else ''
        second_col = row[1].strip() if len(row) > 1 and row[1] else ''
        # Also check combined for headers that span columns
        combined_check = (first_col + ' ' + second_col).strip().upper()
        
        # Metadata section
        if 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in first_col.upper() or 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in second_col.upper() or 'SHOW-TO-PLATFORM ATTRIBUTION RESULTS' in combined_check:
            current_section = 'metadata'
            print(f"   ✅ Entered metadata section at row {i}")
            continue
        elif current_section == 'metadata':
            if 'Show/Content Tracked' in first_col:
                parsed['metadata']['show'] = row[1].strip() if len(row) > 1 else ''
            elif 'Platform Tracked' in first_col or 'platform tracked' in first_col.lower():
                # Try multiple columns for platform
                platform_val = ''
                if len(row) > 1:
                    platform_val = row[1].strip()
                if not platform_val and len(row) > 2:
                    platform_val = row[2].strip()
                if not platform_val and len(row) > 0:
                    # Sometimes the platform might be in the same cell after a colon
                    if ':' in first_col:
                        parts = first_col.split(':', 1)
                        if len(parts) > 1:
                            platform_val = parts[1].strip()
                parsed['metadata']['platform'] = platform_val
                print(f"   📱 Found platform: '{platform_val}' from row {i}: {row[:3]}")
            elif 'Analysis Date Range' in first_col or 'Date Range' in first_col or 'date range' in first_col.lower():
                # Try multiple columns for date range
                date_range_val = ''
                if len(row) > 1:
                    date_range_val = row[1].strip()
                if not date_range_val and len(row) > 2:
                    date_range_val = row[2].strip()
                if not date_range_val and len(row) > 0:
                    # Sometimes the date might be in the same cell after a colon
                    if ':' in first_col:
                        parts = first_col.split(':', 1)
                        if len(parts) > 1:
                            date_range_val = parts[1].strip()
                parsed['metadata']['date_range'] = date_range_val
                print(f"   📅 Found date range: '{date_range_val}' from row {i}: {row[:3]}")
            elif 'Exclusion Window' in first_col:
                parsed['metadata']['exclusion_window'] = row[1].strip() if len(row) > 1 else ''
            elif 'Attribution Window' in first_col:
                parsed['metadata']['attribution_window'] = row[1].strip() if len(row) > 1 else ''
            elif 'KEY METRICS' in first_col.upper() or 'KEY METRICS' in second_col.upper() or 'KEY METRICS' in combined_check:
                current_section = 'key_metrics'
                print(f"   ✅ Entered KEY METRICS section at row {i}: first_col='{first_col}', second_col='{second_col}'")
                continue
        
        # Key metrics
        elif current_section == 'key_metrics':
            if 'Total Show Watchers' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found Total Show Watchers: row={row[:3]}, count={count_val}, gen_pop={gen_pop_val}, row_len={len(row)}")
                parsed['key_metrics']['total_watchers'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Pre-Existing' in first_col or 'Pre Existing' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found Pre-Existing Series Viewers: count={count_val}, gen_pop={gen_pop_val}")
                parsed['key_metrics']['pre_existing'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Clean Sample' in first_col or 'Clean Sample (New First Time Viewers)' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found Clean Sample: count={count_val}, gen_pop={gen_pop_val}")
                parsed['key_metrics']['clean_sample'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'New Platform Signups' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found New Platform Signups: count={count_val}, gen_pop={gen_pop_val}")
                parsed['key_metrics']['new_signups'] = {
                    'count': count_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Clean Conversion Rate' in first_col:
                parsed['key_metrics']['clean_conversion_rate'] = row[1].strip() if len(row) > 1 else ''
            elif 'Total Show Conversion Rate' in first_col:
                parsed['key_metrics']['total_conversion_rate'] = row[1].strip() if len(row) > 1 else ''
            elif 'Average Days' in first_col:
                parsed['key_metrics']['avg_days_to_signup'] = row[3].strip() if len(row) > 3 else ''
            elif 'PER-EPISODE ATTRIBUTION' in first_col.upper() or 'PER-EPISODE ATTRIBUTION' in second_col.upper() or 'PER-EPISODE ATTRIBUTION' in combined_check:
                current_section = 'episode_attribution'
                print(f"   ✅ Entered PER-EPISODE ATTRIBUTION section at row {i}: first_col='{first_col}', second_col='{second_col}'")
                continue
        
        # Episode attribution
        elif current_section == 'episode_attribution':
            # Handle both "Episode X" and just "X" formats
            episode_num = None
            if first_col.startswith('Episode '):
                episode_num = first_col.replace('Episode ', '').strip()
            elif first_col and first_col.strip().isdigit():
                episode_num = first_col.strip()
            elif first_col and len(first_col) <= 3 and first_col.replace(' ', '').isdigit():
                episode_num = first_col.replace(' ', '').strip()
            
            if episode_num:
                signups_val = parse_number(row[1]) if len(row) > 1 else None
                print(f"   📊 Found Episode {episode_num}: signups={signups_val}, row={row[:3]}")
                parsed['episode_attribution'].append({
                    'episode': episode_num,
                    'signups': signups_val,
                    'days_avg': row[3].strip() if len(row) > 3 else '',
                    'min_avg_view': row[5].strip() if len(row) > 5 else '',
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                })
            elif 'ATTRIBUTION SUMMARY' in first_col.upper() or 'ATTRIBUTION SUMMARY' in second_col.upper() or 'ATTRIBUTION SUMMARY' in combined_check:
                current_section = 'attribution_summary'
                print(f"   ✅ Entered ATTRIBUTION SUMMARY section at row {i}: first_col='{first_col}', second_col='{second_col}'")
                continue
        
        # Attribution summary
        elif current_section == 'attribution_summary':
            if 'Attributed Signups' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                pct_val = row[7].strip() if len(row) > 7 else ''
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found Attributed Signups: count={count_val}, pct={pct_val}, gen_pop={gen_pop_val}")
                parsed['attribution_summary']['attributed'] = {
                    'count': count_val,
                    'percentage': pct_val,
                    'gen_pop': gen_pop_val
                }
            elif 'Dormant to Reactive' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                pct_val = row[7].strip() if len(row) > 7 else ''
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found Dormant to Reactive: count={count_val}, pct={pct_val}, gen_pop={gen_pop_val}")
                parsed['attribution_summary']['dormant_reactive'] = {
                    'count': count_val,
                    'percentage': pct_val,
                    'gen_pop': gen_pop_val
                }
            elif 'TOTAL SIGNUPS' in first_col:
                count_val = parse_number(row[1]) if len(row) > 1 else None
                pct_val = row[7].strip() if len(row) > 7 else ''
                gen_pop_val = row[8].strip() if len(row) > 8 else ''
                print(f"   📊 Found TOTAL SIGNUPS: count={count_val}, pct={pct_val}, gen_pop={gen_pop_val}")
                parsed['attribution_summary']['total'] = {
                    'count': count_val,
                    'percentage': pct_val,
                    'gen_pop': gen_pop_val
                }
            elif 'SIGNUP TIMING (Days After Show is Available)' in first_col or 'SIGNUP TIMING (Days After Show is Available)' in second_col:
                current_section = 'signup_timing'
                continue
        
        # Signup timing
        elif current_section == 'signup_timing':
            if first_col and first_col not in ['', 'SIGNUP TIMING (Days After Show is Available)']:
                if 'Days Later' in first_col or first_col in ['Same Day', 'Day 1']:
                    parsed['signup_timing'].append({
                        'timing': first_col,
                        'signups': row[1].strip() if len(row) > 1 else '',
                        'percentage': row[7].strip() if len(row) > 7 else '',
                        'gen_pop': row[8].strip() if len(row) > 8 else ''
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
                parsed['episode_signup_timing'][current_episode].append({
                    'timing': first_col,
                    'signups': row[1].strip() if len(row) > 1 else '',
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                })
            elif 'POST-SIGNUP TOUCHPOINT ANALYSIS' in first_col or 'POST-SIGNUP TOUCHPOINT ANALYSIS' in second_col:
                current_section = 'post_signup_touchpoints'
                continue
        
        # Post-signup touchpoints
        elif current_section == 'post_signup_touchpoints':
            if first_col and first_col.endswith('Touchpoint'):
                touchpoint_num = first_col.replace('Touchpoint', '').strip()
                parsed['post_signup_touchpoints'].append({
                    'touchpoint': touchpoint_num,
                    'users': row[1].strip() if len(row) > 1 else '',
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                })
            elif 'Total Platform Signups' in first_col:
                parsed['post_signup_touchpoints'].append({
                    'touchpoint': 'Total',
                    'users': row[1].strip() if len(row) > 1 else '',
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                })
            elif 'COMPETITIVE PLATFORMS' in first_col or 'COMPETITIVE PLATFORMS' in second_col:
                current_section = 'competitive_platforms'
                continue
        
        # Competitive platforms
        elif current_section == 'competitive_platforms':
            if first_col and first_col not in ['', 'COMPETITIVE PLATFORMS (% of Show Watchers)']:
                if first_col and not first_col.startswith(','):
                    platform = first_col.strip()
                    percentage = row[7].strip() if len(row) > 7 else ''
                    parsed['competitive_platforms'].append({
                        'platform': platform,
                        'percentage': percentage
                    })
            elif 'MONTHLY PLATFORM SIGNUPS' in first_col or 'MONTHLY PLATFORM SIGNUPS' in second_col:
                current_section = 'monthly_signups'
                continue
        
        # Monthly signups
        elif current_section == 'monthly_signups':
            if first_col and first_col not in ['', 'MONTHLY PLATFORM SIGNUPS -']:
                if re.match(r'^\d{4}-\d{2}$', first_col):
                    parsed['monthly_signups'].append({
                        'month': first_col,
                        'signups': row[1].strip() if len(row) > 1 else '',
                        'watched_show': row[3].strip() if len(row) > 3 else '',
                        'percentage': row[7].strip() if len(row) > 7 else '',
                        'gen_pop': row[8].strip() if len(row) > 8 else ''
                    })
            elif 'MONTHLY PLATFORM CHURN' in first_col or 'MONTHLY PLATFORM CHURN' in second_col:
                current_section = 'monthly_churn'
                continue
        
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
                
                # Only add if it's not a gender entry and looks like an age range
                if not any(keyword in first_col_upper for keyword in gender_keywords):
                    # Check if it looks like an age range (contains numbers or age-like patterns)
                    if any(char.isdigit() for char in first_col) or '-' in first_col or '+' in first_col or 'to' in first_col_upper or 'and' in first_col_upper:
                        parsed['demographics']['age'].append({
                            'age_range': first_col,
                            'count': row[1].strip() if len(row) > 1 else '',
                            'percentage': row[7].strip() if len(row) > 7 else '',
                            'gen_pop': row[8].strip() if len(row) > 8 else ''
                        })
                    else:
                        print(f"   ⚠️ Skipping potential gender entry in age section: '{first_col}'")
                else:
                    # This is a gender entry - add it to gender data instead
                    print(f"   ⚠️ Found gender entry '{first_col}' in age section, moving to gender data")
                    parsed['demographics']['gender'].append({
                        'gender': first_col,
                        'count': row[1].strip() if len(row) > 1 else '',
                        'percentage': row[7].strip() if len(row) > 7 else '',
                        'gen_pop': row[8].strip() if len(row) > 8 else ''
                    })
        
        elif current_section == 'demographics_gender':
            if first_col and first_col not in ['', 'GENDER']:
                parsed['demographics']['gender'].append({
                    'gender': first_col,
                    'count': row[1].strip() if len(row) > 1 else '',
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
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
    
    # Fallback: Try to find attribution summary
    if not parsed['attribution_summary'].get('total'):
        for i, row in enumerate(rows):
            if not row or len(row) < 2:
                continue
            first_col = row[0].strip() if row[0] else ''
            if 'TOTAL SIGNUPS' in first_col.upper() and not parsed['attribution_summary'].get('total'):
                parsed['attribution_summary']['total'] = {
                    'count': parse_number(row[1]) if len(row) > 1 else None,
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                }
                print(f"   ✅ Fallback: Found TOTAL SIGNUPS")
            elif 'Attributed Signups' in first_col and not parsed['attribution_summary'].get('attributed'):
                parsed['attribution_summary']['attributed'] = {
                    'count': parse_number(row[1]) if len(row) > 1 else None,
                    'percentage': row[7].strip() if len(row) > 7 else '',
                    'gen_pop': row[8].strip() if len(row) > 8 else ''
                }
                print(f"   ✅ Fallback: Found Attributed Signups")
    
    # Fallback: Try to find episodes if none were found
    if len(parsed['episode_attribution']) == 0:
        print("   ⚠️ No episodes found via section detection, trying fallback parsing...")
        for i, row in enumerate(rows):
            if not row or len(row) < 2:
                continue
            first_col = row[0].strip() if row[0] else ''
            # Look for rows that start with a number (episode number)
            if first_col and (first_col.isdigit() or first_col.startswith('Episode ')):
                episode_num = first_col.replace('Episode ', '').strip() if first_col.startswith('Episode ') else first_col
                # Check if row[1] looks like a number (signups count)
                if row[1] and (row[1].strip().replace(',', '').isdigit()):
                    signups_val = parse_number(row[1])
                    if signups_val and signups_val > 0:  # Only add if it looks like real data
                        parsed['episode_attribution'].append({
                            'episode': episode_num,
                            'signups': signups_val,
                            'days_avg': row[3].strip() if len(row) > 3 else '',
                            'min_avg_view': row[5].strip() if len(row) > 5 else '',
                            'percentage': row[7].strip() if len(row) > 7 else '',
                            'gen_pop': row[8].strip() if len(row) > 8 else ''
                        })
                        print(f"   ✅ Fallback: Found Episode {episode_num} with {signups_val} signups")
    
    # Log parsing summary
    print(f"📊 Parsing complete:")
    print(f"   Key metrics: {len(parsed['key_metrics'])} items")
    print(f"   Episodes: {len(parsed['episode_attribution'])} items")
    print(f"   Signup timing: {len(parsed['signup_timing'])} items")
    print(f"   Attribution summary: {len(parsed['attribution_summary'])} items")
    if parsed['key_metrics'].get('total_watchers'):
        print(f"   Total Watchers: {parsed['key_metrics']['total_watchers']}")
    
    return parsed


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
                # Skip historic folder and non-CSV files
                if key.startswith('historic/') or not key.endswith('.csv'):
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
        
        # Log what was parsed in detail
        print(f"📊 Parsed data summary:")
        print(f"   Metadata keys: {list(parsed.get('metadata', {}).keys())}")
        print(f"   Key metrics keys: {list(parsed.get('key_metrics', {}).keys())}")
        print(f"   Key metrics values: {parsed.get('key_metrics', {})}")
        print(f"   Episodes count: {len(parsed.get('episode_attribution', []))}")
        print(f"   Signup timing count: {len(parsed.get('signup_timing', []))}")
        print(f"   Attribution summary keys: {list(parsed.get('attribution_summary', {}).keys())}")
        print(f"   Attribution summary: {parsed.get('attribution_summary', {})}")
        
        # Check if data is actually empty
        has_data = any([
            parsed.get('key_metrics', {}),
            parsed.get('episode_attribution', []),
            parsed.get('signup_timing', []),
            parsed.get('attribution_summary', {})
        ])
        print(f"   Has any data: {has_data}")
        
        # Extract show name from filename
        name_without_ext = s3_key.replace('.csv', '')
        match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
        if match:
            show_name = match.group(1).replace('_', ' ')
        else:
            show_name = name_without_ext.replace('_', ' ')
        
        # Get date range from metadata
        date_range = parsed['metadata'].get('date_range', '')
        
        print(f"✅ Returning subscriber IQ data for show: {show_name.upper()}")
        print(f"   Data keys: {list(parsed.keys())}")
        print(f"   Data structure check - key_metrics type: {type(parsed.get('key_metrics'))}")
        print(f"   Data structure check - key_metrics content: {parsed.get('key_metrics')}")
        
        response_data = {
            'success': True,
            'data': parsed,
            'show': show_name.upper(),
            'date_range': date_range,
            's3_key': s3_key
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
            if ticker_key in metadata:
                ticker['display_name'] = metadata[ticker_key].get('display_name', ticker['ticker'])
                ticker['kpi'] = metadata[ticker_key].get('kpi', ticker['kpi'])
                ticker['parent_ticker'] = metadata[ticker_key].get('parent_ticker', None)
            else:
                ticker['parent_ticker'] = None
        
        print(f"✅ Found {len(tickers)} tickers")
        return jsonify({'success': True, 'tickers': tickers})
        
    except Exception as e:
        print(f"❌ Error listing hedge fund tickers: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/data/<path:s3_key>')
@requires_auth
def get_hedge_fund_ticker_data(s3_key):
    """Get ticker CSV data and calculate day-over-day metrics."""
    print(f"📥 get_hedge_fund_ticker_data called for: {s3_key}")
    
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
        
        # Pre-calculate all stats for immediate display (no waiting on frontend)
        calculated_stats = {}
        if len(data) > 0:
            latest = data[-1]
            latest_quarter = latest.get('Quarter', 'N/A')
            
            # Filter to current quarter
            current_quarter_data = [d for d in data if d.get('Quarter') == latest_quarter]
            
            if len(current_quarter_data) > 0:
                # Calculate cumulative stats for current quarter
                quarter_subs = sum(d.get('Total Subs', 0) for d in current_quarter_data)
                quarter_cancels = sum(d.get('Total Cancels', 0) for d in current_quarter_data)
                quarter_net_growth = quarter_subs - quarter_cancels
                quarter_start_consumers = current_quarter_data[0].get('Total Consumers', 1)
                net_growth_pct = (quarter_net_growth / quarter_start_consumers * 100) if quarter_start_consumers > 0 else 0
                
                # Calculate projected net growth rate
                days_in_quarter = len(current_quarter_data)
                avg_daily_net_growth = quarter_net_growth / days_in_quarter if days_in_quarter > 0 else 0
                
                # Determine total days in quarter
                total_days_in_quarter = 92 if 'Q4' in latest_quarter else 90
                
                # Projected net growth
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
                        # Calculate quarterly net growth % for all quarters
                        quarters = {}
                        for d in data:
                            q = d.get('Quarter')
                            if q and q != 'N/A':
                                if q not in quarters:
                                    quarters[q] = {'subs': 0, 'cancels': 0, 'start_consumers': None}
                                quarters[q]['subs'] += d.get('Total Subs', 0)
                                quarters[q]['cancels'] += d.get('Total Cancels', 0)
                                if quarters[q]['start_consumers'] is None:
                                    quarters[q]['start_consumers'] = d.get('Total Consumers', 0)
                        
                        # Calculate variance for all quarters with SEC actuals
                        all_variances = []
                        quarter_specific_variances = []
                        
                        # Extract current quarter number (e.g., "Q1" from "Q1 2026")
                        current_quarter_num = latest_quarter.split()[0] if latest_quarter else None
                        
                        for quarter, q_data in quarters.items():
                            if quarter in ticker_actuals:
                                # Calculate our growth %
                                net_growth = q_data['subs'] - q_data['cancels']
                                start_consumers = q_data['start_consumers']
                                our_growth_pct = (net_growth / start_consumers * 100) if start_consumers > 0 else 0
                                
                                # Get SEC actual % (already a percentage)
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
            's3_key': s3_key,
            'bucket': HEDGE_FUND_S3_BUCKET,
            'calculated_stats': calculated_stats  # Pre-calculated stats for instant display
        }
        
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
        display_name = data.get('display_name')
        kpi = data.get('kpi')
        parent_ticker = data.get('parent_ticker')
        relevance_percentage = data.get('relevance_percentage')
        
        if not s3_key:
            return jsonify({'success': False, 'error': 's3_key is required'}), 400
        
        metadata = load_ticker_metadata()
        
        if s3_key not in metadata:
            metadata[s3_key] = {}
        
        if display_name:
            metadata[s3_key]['display_name'] = display_name
        if kpi:
            metadata[s3_key]['kpi'] = kpi
        if parent_ticker is not None:  # Allow empty string to clear
            metadata[s3_key]['parent_ticker'] = parent_ticker
        if relevance_percentage is not None:  # Allow 0 or empty string to clear
            # Validate it's a number between 0-100
            try:
                rel_pct = float(relevance_percentage) if relevance_percentage != '' else None
                if rel_pct is not None and (rel_pct < 0 or rel_pct > 100):
                    return jsonify({'success': False, 'error': 'Relevance percentage must be between 0-100'}), 400
                metadata[s3_key]['relevance_percentage'] = rel_pct
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'Invalid relevance percentage format'}), 400
        
        save_ticker_metadata(metadata)
        
        return jsonify({'success': True, 'message': 'Metadata updated', 'metadata': metadata[s3_key]})


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
        return
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=TICKER_METADATA_KEY,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
    except Exception as e:
        print(f"❌ Error saving ticker metadata: {e}")


@app.route('/api/hedge-fund-iq/predict-earnings', methods=['POST'])
@requires_auth
def predict_earnings():
    """Use AI to predict earnings beat/miss based on KPI data."""
    client = get_openai_client()
    if not client:
        return jsonify({'success': False, 'error': 'OpenAI not configured'}), 500
    
    try:
        data = request.get_json()
        ticker = data.get('ticker')
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
        
        return jsonify({
            'success': True,
            'prediction': result.get('prediction', 'UNKNOWN'),
            'confidence': result.get('confidence', 50),
            'analysis': result.get('analysis', 'Analysis unavailable')
        })
        
    except Exception as e:
        print(f"❌ Error in predict_earnings: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/sec-actuals/<ticker>')
@requires_auth
def get_sec_actuals(ticker):
    """Get SEC actuals for a ticker."""
    try:
        # Load SEC actuals from S3
        all_actuals = load_json_from_s3(SEC_ACTUALS_FILE)
        return jsonify({
            'success': True,
            'actuals': all_actuals.get(ticker, {})
        })
    except Exception as e:
        print(f"❌ Error getting SEC actuals: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hedge-fund-iq/sec-actuals', methods=['POST'])
@requires_admin
def update_sec_actuals():
    """Update SEC actuals for a ticker and quarter (admin only)."""
    try:
        data = request.get_json()
        ticker = data.get('ticker')
        quarter = data.get('quarter')
        actual_value = data.get('actual_value')
        
        if not ticker or not quarter:
            return jsonify({'success': False, 'error': 'Ticker and quarter required'}), 400
        
        # Load existing actuals from S3
        all_actuals = load_json_from_s3(SEC_ACTUALS_FILE)
        
        # Update actuals for this ticker
        if ticker not in all_actuals:
            all_actuals[ticker] = {}
        
        if actual_value is None or actual_value == '':
            # Remove the entry if value is empty
            if quarter in all_actuals[ticker]:
                del all_actuals[ticker][quarter]
        else:
            all_actuals[ticker][quarter] = float(actual_value)
        
        # Save back to S3
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
            'tickers': quick_selects.get('tickers', {})
        })
    except Exception as e:
        print(f"❌ Error loading quick selects: {e}")
        return jsonify({
            'success': True,  # Return success with empty data if file doesn't exist
            'profiles': {},
            'tickers': {}
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
        
        quick_selects = {
            'profiles': profiles,
            'tickers': tickers,
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
        # Extract key behavioral data
        behavioral_data = []
        demographic_data = {}
        
        for record in csv_data[:500]:  # Limit to first 500 rows
            col = str(record.get('Column', '')).upper()
            val = record.get('Value', '')
            idx = record.get('Index', 0)
            
            if 'BEHAVIORAL' in col or 'INTEREST' in col:
                if isinstance(val, (int, float)) and val > 0:
                    behavioral_data.append({
                        'item': str(record.get('Column', '')),
                        'index': float(idx) if idx else 0,
                        'category': str(record.get('Category', 'Other'))
                    })
            elif 'DEMOGRAPHIC' in col or 'AGE' in col or 'GENDER' in col or 'INCOME' in col:
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

BEHAVIORAL DATA (Top 100 items by index):
{json.dumps(top_items[:100], indent=2)}

DEMOGRAPHIC DATA:
{json.dumps(demographic_data, indent=2)}

Provide your analysis in JSON format:
{{
    "question1_beforeAfter": "Detailed insight about what happens before and after engagement",
    "question2_substitution": "Detailed insight about cross-category substitution and occasion leakage",
    "question3_culturalMomentum": "Detailed insight about pre-trend cultural momentum signals",
    "question4_attentionEmotion": "Detailed insight about attention and emotional receptivity",
    "question5_relevanceLoss": "Detailed insight about who is losing relevance and why",
    "question6_whiteSpace": "Detailed insight about white-space occasions and opportunities",
    "executiveSummary": "2-3 sentence high-level summary of the most critical insights",
    "keyRecommendations": ["Strategic recommendation 1", "Strategic recommendation 2", "Strategic recommendation 3"]
}}

Be specific, data-driven, strategic, and actionable. Reference specific items/brands when relevant. Think like a CMO, not a data analyst."""

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
        
        # Prepare structured data for AI
        summary_data = {
            'primaryProfile': {
                'name': primary_profile.get('name', 'Unknown'),
                'sampleSize': primary_profile.get('sampleSize', 0),
                'projectedUS': primary_profile.get('projectedUS', 0),
                'medianAge': primary_profile.get('medianAge', 'N/A'),
                'demographics': primary_profile.get('demographics', {}),
                'demographicsProjection': primary_profile.get('demographicsProjection', {}),
                'topCategories': primary_profile.get('topCategories', []),
                'topInterests': primary_profile.get('topInterests', [])
            },
            'competitors': [
                {
                    'name': comp.get('name', 'Unknown'),
                    'demographics': comp.get('demographics', {}),
                    'demographicsProjection': comp.get('demographicsProjection', {}),
                    'topCategories': comp.get('topCategories', []),
                    'topInterests': comp.get('topInterests', [])
                }
                for comp in competitors
            ],
            'genPop': {
                'demographics': gen_pop_profile.get('demographics', {}) if gen_pop_profile else {},
                'demographicsProjection': gen_pop_profile.get('demographicsProjection', {}) if gen_pop_profile else {},
                'topCategories': gen_pop_profile.get('topCategories', []) if gen_pop_profile else [],
                'topInterests': gen_pop_profile.get('topInterests', []) if gen_pop_profile else []
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

IMPORTANT: The "Other" category represents the absence of data and should NEVER be used in analysis, comparisons, or callouts. Ignore any "Other" category entries completely.

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
    """Get profile data from S3 dashboard-inputs bucket and return pre-signed URL for direct access."""
    try:
        print(f"📊 Loading profile: {filename}")
        
        # Ensure filename has .csv extension
        if not filename.endswith('.csv'):
            filename = f"{filename}.csv"
        
        # Check if file exists
        try:
            s3_client.head_object(Bucket='dashboard-inputs', Key=filename)
        except s3_client.exceptions.NoSuchKey:
            print(f"❌ Profile not found: {filename}")
            return jsonify({'success': False, 'error': f'Profile file not found: {filename}'}), 404
        
        # Generate a pre-signed URL for the file (valid for 1 hour)
        try:
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': 'dashboard-inputs',
                    'Key': filename,
                    'ResponseContentType': 'text/csv'
                },
                ExpiresIn=3600  # 1 hour
            )
            
            print(f"✅ Generated presigned URL for {filename}")
            print(f"🔗 URL: {presigned_url[:100]}...")
            
            return jsonify({
                'success': True,
                'filename': filename,
                'url': presigned_url
            })
        except Exception as e:
            print(f"❌ Error generating presigned URL: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Failed to generate URL: {str(e)}'}), 500
        
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
        
        # Automatically search for similar files for demographic consistency
        reference_demographics = None
        reference_sample_size = None
        reference_file_key = None
        
        # Search S3 for existing runs with same brand
        try:
            print(f"🔍 Checking for existing runs of '{brands[0]}' for consistency validation...")
            exact_match, similar_files = check_s3_for_existing(brands[0], start_date, end_date)
            
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
                  reference_demographics, reference_sample_size, reference_file_key)
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

def load_profile_image_cache():
    """Load profile image cache from S3."""
    global profile_image_cache
    if not s3_client:
        return False
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_IMAGE_CACHE_KEY)
        profile_image_cache = json.loads(response['Body'].read().decode('utf-8'))
        print(f"✅ Loaded profile image cache: {len(profile_image_cache)} images")
        return True
    except:
        print("📂 No profile image cache found")
        return False

def save_profile_image_cache():
    """Save profile image cache to S3."""
    global profile_image_cache, profile_image_cache_dirty
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
    
    try:
        # Save the current cache state
        cache_json = json.dumps(profile_image_cache, indent=2)
        cache_size = len(cache_json)
        print(f"   💾 Writing cache to S3: {len(profile_image_cache)} entries, {cache_size} bytes")
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=S3_IMAGE_CACHE_KEY,
            Body=cache_json,
            ContentType='application/json'
        )
        profile_image_cache_dirty = False
        print(f"   ✅ Successfully wrote cache to S3: {S3_IMAGE_CACHE_KEY}")
        print(f"💾 Saved profile image cache: {len(profile_image_cache)} images")
        
        # Log sample of what was saved
        sample_keys = list(profile_image_cache.keys())[:5]
        print(f"   📋 Sample keys saved: {sample_keys}")
        
        return True
    except Exception as e:
        print(f"⚠️ Error saving profile image cache: {e}")
        import traceback
        traceback.print_exc()
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
        
        # Ensure cache is loaded before updating (but preserve any entries we might have)
        if not profile_image_cache:
            load_profile_image_cache()
        
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
        
        # Add to cache
        profile_image_cache[cache_key] = new_entry
        profile_image_cache_dirty = True
        
        print(f"   📝 Added entry to cache: {cache_key} -> {image_url}")
        print(f"   📊 Cache now has {len(profile_image_cache)} entries before save")
        
        # Save immediately and verify
        saved = save_profile_image_cache()
        
        # Ensure our entry is still there after save (in case save function reloaded cache)
        if cache_key not in profile_image_cache or profile_image_cache[cache_key] != new_entry:
            print(f"   ⚠️ WARNING: Entry was lost during save! Restoring...")
            profile_image_cache[cache_key] = new_entry
            # Try saving again
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
    """Rename a file in S3 (copy to new key, delete old)."""
    global s3_cache
    
    try:
        data = request.get_json()
        old_key = data.get('old_key')
        new_name = data.get('new_name')
        display_name = data.get('display_name')  # Optional display name override
        
        if not old_key or not new_name:
            return jsonify({'success': False, 'error': 'Old key and new name required'})
        
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
        if s3_cache and 'jobs' in s3_cache:
            for job in s3_cache.get('jobs', []):
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
                    job['project_name'] = new_display_name
                    job['brand'] = new_display_name
                    print(f"✅ Updated display name to: {new_display_name}")
                    break
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

# Hedge Fund IQ ticker metadata storage key
TICKER_METADATA_KEY = 'system/ticker_metadata.json'

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

@app.route('/api/admin/change-category', methods=['POST'])
@requires_admin
def change_file_category():
    """Change the BRAND CATEGORY in a CSV file or SVOD file metadata."""
    global s3_cache
    
    try:
        data = request.get_json()
        file_key = data.get('file_key')
        new_category = data.get('new_category', '').strip().upper()
        
        if not file_key or not new_category:
            return jsonify({'success': False, 'error': 'File key and category required'})
        
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
        
        # If no BRAND CATEGORY line found, add it at the beginning
        if not updated:
            # Find where to insert (after any header lines, before data)
            insert_idx = 0
            for i, line in enumerate(new_lines):
                if line.strip() and not line.startswith('#'):
                    insert_idx = i
                    break
            new_lines.insert(insert_idx, f'BRAND CATEGORY,{new_category}')
        
        # Upload updated content back to S3
        updated_content = '\n'.join(new_lines)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=file_key,
            Body=updated_content.encode('utf-8'),
            ContentType='text/csv'
        )
        
        # Update cache - find job in s3_cache and update its category
        if s3_cache and 'jobs' in s3_cache:
            for job in s3_cache.get('jobs', []):
                if job.get('key') == file_key or job.get('s3_key') == file_key:
                    job['category'] = new_category
                    break
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
    if not profile_image_cache:
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
            'jobs': s3_cache['jobs'],
            'categories': s3_cache['categories'],
            'last_updated': s3_cache['last_updated'],
            'file_count': s3_cache['file_count'],
            'last_full_scan': s3_cache['last_full_scan']
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
        
        for _, row in df.iterrows():
            category = str(row.get('Column', '')).upper()
            value = row.get('Value', '')
            pct = float(row.get('Brand Penetration (Row)', 0) or 0)
            
            if category == 'SAMPLE SIZE':
                summary['sampleSize'] = int(row.get('Original Raw Numbers', 0) or row.get('Category Share', 0) or 0)
            elif category == 'BRAND INPUT':
                summary['projectedUS'] = int(row.get('US Gen Pop Projection', 0) or 0)
            elif category == 'GENDER' and value:
                summary['gender'][value] = pct
            elif category == 'AGE' and value:
                summary['age'][value] = pct
            elif category == 'INCOME' and value:
                summary['income'][value] = pct
            elif category == 'ETHNICITY' and value:
                summary['ethnicity'][value] = pct
        
        return summary
    except Exception as e:
        print(f"Error extracting demographics: {e}")
        return None

# Cache loading is handled in background thread - see quick_startup_cache()


@app.route('/api/jobs')
@requires_auth
def list_jobs():
    """List all jobs (local + S3 cached) with caching for performance."""
    import time
    
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
    
    # Update user's last activity time (but don't block on it)
    username = session.get('username')
    if username:
        try:
            users = load_users()
            if username in users:
                users[username]['last_activity'] = time.time()
                save_users(users)
        except:
            pass  # Don't block on activity tracking
    
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
    
    # Use persisted cache only - no S3 scanning on page load for speed
    # If cache is empty, try to load persisted cache
    if not s3_cache['jobs'] and s3_client:
        load_persisted_cache()
    
    # Don't auto-refresh - user can manually refresh if needed
    # This makes page loads instant
    
    # Add cached S3 jobs
    job_list.extend(s3_cache['jobs'])
    for cat in s3_cache['categories']:
        categories.add(cat)
    
    # Sort by created_at descending
    sorted_jobs = sorted(job_list, key=lambda x: x['created_at'], reverse=True)
    
    return jsonify({
        'jobs': sorted_jobs,
        'categories': sorted(list(categories)),
        'cache_info': {
            'last_updated': s3_cache.get('last_updated'),
            'file_count': s3_cache.get('file_count', 0),
            'cached': True
        }
    })


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
            'category': job.get('category', 'Uncategorized'),
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
    
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv') or key.startswith('system/') or key.startswith('historic/'):
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
                    print(f"   ➕ New: {key}")
                    
                elif existing[key] != obj_modified:
                    # MODIFIED file - preserve existing metadata
                    job_data = process_s3_file_metadata(key, obj)
                    job_data['last_modified'] = obj_modified
                    # Update in place, preserving custom metadata
                    for i, job in enumerate(s3_cache['jobs']):
                        if job.get('s3_key') == key:
                            # Preserve custom image and manually set category if they exist
                            if job.get('custom_image'):
                                job_data['custom_image'] = job['custom_image']
                            # Keep existing category if it was manually set (not UNCATEGORIZED)
                            if job.get('category') and job.get('category') != 'UNCATEGORIZED':
                                job_data['category'] = job['category']
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
                if not key.endswith('.csv') or key.startswith('system/'):
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
    
    return {
        'job_id': key,
        'project_name': project_name,
        'status': 'cached',
        'progress': 100,
        'created_at': obj['LastModified'].isoformat(),
        'source': 's3',
        's3_key': key,
        'category': category
    }


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
                 previous_file_path=None, reference_demographics=None, reference_sample_size=None,
                 reference_file_key=None):
    """Run the behavioral graph analysis pipeline with demographic consistency validation."""
    try:
        update_job_status(job_id, status='running', progress=5, message='Initializing...')
        
        # Import the bg module
        try:
            import bg
            import random
            import numpy as np
            from config import SNOWFLAKE_CONFIG
        except ImportError as e:
            update_job_status(job_id, status='failed', error=f'Module import failed: {str(e)}')
            return
        
        # ========== DETERMINISTIC SEEDING (matches terminal behavior) ==========
        # Create consistent random seed based on inputs for reproducible results
        seed_string = f"{brands[0]}_{sample_start}_{sample_end}_{behavior_start}_{behavior_end}" if brands else f"{sample_start}_{sample_end}_{behavior_start}_{behavior_end}"
        deterministic_seed = hash(seed_string) % (2**32)  # Convert to 32-bit integer
        random.seed(deterministic_seed)
        np.random.seed(deterministic_seed)
        print(f"🎲 Deterministic seed set: {deterministic_seed}")
        
        # If we have a reference file from S3, download it for consistency enforcement
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
        
        try:
            if hasattr(bg, 'perform_full_universe_scan'):
                print("🔍 Performing full universe scan (matching terminal behavior)...")
                universe_results = bg.perform_full_universe_scan(conn, brands, sample_start, sample_end, purchasers_only)
                if universe_results:
                    print(f"🌍 Universe scan complete. True universe size: {universe_results['total_universe']:,} users")
                    # Store universe size for use in pipeline (same as terminal)
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
        
        # Run the full pipeline with reference file for consistency
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
                brand_category=brand_category
            )
            
            update_job_status(job_id, progress=85, message='Processing results...')
            
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
                        
                        # Apply listener/watcher/player adjustments
                        if is_listener_watcher:
                            if hasattr(bg, 'set_brand_input_to_csv'):
                                df = bg.set_brand_input_to_csv(df)
                            if platform_name and hasattr(bg, 'adjust_platform_to_100_percent'):
                                df = bg.adjust_platform_to_100_percent(df, platform_name)
                        
                        df.to_csv(result_file, index=False)
                        print("✅ Frequency analysis complete")
                    except Exception as e:
                        print(f"⚠️ Frequency analysis error: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Apply listener/watcher adjustments even without frequency analysis
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
                
                # ========== POST-PROCESSING (matches terminal behavior exactly) ==========
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
                        "BEAUTY/WELLNESS", "BRAND CATEGORY", "HOME/OUTDOOR", "MOST PURCHASED CATEGORIES", 
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
                    
                    # Save the fully processed file
                    df.to_csv(result_file, index=False)
                    print("✅ All post-processing complete (matching terminal behavior)")
                    
                except Exception as e:
                    print(f"⚠️ Post-processing error (non-fatal): {e}")
                    import traceback
                    traceback.print_exc()
                
                # Validate demographics against reference if provided
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
# STARTUP CACHE LOADING (Async - doesn't block app startup)
# ============================================================================

import threading
import time as time_module

cache_loading_complete = False
BACKGROUND_CHECK_INTERVAL = 300  # Check for new files every 5 minutes

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
            
            # Try Wikipedia
            if not image_url:
                search_name = re.sub(r'@\w+', '', profile_name).replace('_', ' ').replace('-', ' ').strip()
                if search_name:
                    try:
                        wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(search_name)}"
                        req = urllib.request.Request(wiki_url, headers={'User-Agent': 'CrosswalkIQ/1.0'})
                        with urllib.request.urlopen(req, timeout=3) as response:
                            data = json.loads(response.read().decode())
                            if 'thumbnail' in data:
                                image_url = data['thumbnail'].get('source')
                                source = 'wikipedia'
                                title = data.get('title', profile_name)
                            elif 'originalimage' in data:
                                image_url = data['originalimage'].get('source')
                                source = 'wikipedia'
                                title = data.get('title', profile_name)
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
    
    print("🔄 Starting background cache checker (every 5 min)...")
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
                        
                        # Show if owned by user, shared with user, or same company
                        owner = deck_data.get('owner', '')
                        shared_with = deck_data.get('shared_with', [])
                        deck_company = deck_data.get('company', '')
                        is_team_deck = deck_data.get('is_team_deck', False)
                        
                        can_view = (
                            owner == username or
                            username in shared_with or
                            (is_team_deck and deck_company == company)
                        )
                        
                        if can_view:
                            decks.append({
                                'id': deck_data.get('id'),
                                'name': deck_data.get('name', 'Untitled Deck'),
                                'owner': owner,
                                'is_mine': owner == username,
                                'is_team_deck': is_team_deck,
                                'slides_count': len(deck_data.get('slides', [])),
                                'created_at': deck_data.get('created_at'),
                                'updated_at': deck_data.get('updated_at'),
                                'shared_with': shared_with
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
        
        # Check permission
        username = session.get('username')
        company = user.get('company', '')
        owner = deck.get('owner', '')
        shared_with = deck.get('shared_with', [])
        deck_company = deck.get('company', '')
        is_team_deck = deck.get('is_team_deck', False)
        
        can_view = (
            owner == username or
            username in shared_with or
            (is_team_deck and deck_company == company)
        )
        
        if not can_view:
            return jsonify({'success': False, 'error': 'Permission denied'})
        
        deck['can_edit'] = owner == username or username in shared_with
        
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
        
        # Check permission
        username = session.get('username')
        owner = deck.get('owner', '')
        shared_with = deck.get('shared_with', [])
        
        can_edit = owner == username or username in shared_with
        if not can_edit:
            return jsonify({'success': False, 'error': 'Permission denied'})
        
        # Update deck
        data = request.get_json()
        if 'name' in data:
            deck['name'] = data['name']
        if 'description' in data:
            deck['description'] = data['description']
        if 'slides' in data:
            deck['slides'] = data['slides']
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
# ATTRIBUTION IQ ENDPOINTS
# ============================================================================

@app.route('/api/attribution/talent-search', methods=['POST'])
@requires_auth
def submit_talent_search():
    """Submit a Talent Search IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access
        role = user.get('role', 'user')
        if role != 'admin' and role != 'enterprise' and not user.get('has_attribution_iq_access', False):
            return jsonify({'error': 'Attribution IQ access required'}), 403
        
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
        username = user.get('username', 'unknown')
        
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


@app.route('/api/attribution/talent-theater', methods=['POST'])
@requires_auth
def submit_talent_theater():
    """Submit a Talent Ticket Sale IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access
        role = user.get('role', 'user')
        if role != 'admin' and role != 'enterprise' and not user.get('has_attribution_iq_access', False):
            return jsonify({'error': 'Attribution IQ access required'}), 403
        
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
        
        # Create job
        job_id = str(uuid.uuid4())
        username = user.get('username', 'unknown')
        
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
        
        # Start job in background thread
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


def run_talent_theater(job_id):
    """Run the Talent_Theater_Attribution.py script."""
    try:
        update_job_status(job_id, progress=10, message='Initializing...')
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Talent_Theater_Attribution.py')
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
        
        # Run the query function
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
            
            # Write output
            module.write_output(results, script_params)
            
            # Find the output file (it's written to Desktop/attribution folder)
            from pathlib import Path
            output_folder = Path.home() / "Desktop" / "attribution"
            if output_folder.exists():
                # Find the most recent CSV file matching the pattern (movie_talent_timestamp.csv)
                # The file name format is: {safe_movie_name}_{safe_talent_name}_{timestamp}.csv
                csv_files = list(output_folder.glob("*.csv"))
                # Filter to files that match the pattern (have timestamp at end)
                csv_files = [f for f in csv_files if len(f.stem.split('_')) >= 3]
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
        error_msg = f"Error running talent theater: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        update_job_status(job_id, status='failed', error=error_msg)


@app.route('/api/attribution/svod-acquisition', methods=['POST'])
@requires_auth
def submit_svod_acquisition():
    """Submit a SVOD Acquisition IQ analysis job."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Check access
        role = user.get('role', 'user')
        if role != 'admin' and role != 'enterprise' and not user.get('has_attribution_iq_access', False):
            return jsonify({'error': 'Attribution IQ access required'}), 403
        
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
        platform_url_patterns = data.get('platform_url_patterns', [])
        
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
        if not platform_url_patterns:
            return jsonify({'error': 'At least one platform URL pattern is required'}), 400
        
        # Create job
        job_id = str(uuid.uuid4())
        username = user.get('username', 'unknown')
        
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
                'platform_url_patterns': platform_url_patterns
            }
        }
        
        # Start job in background thread
        thread = threading.Thread(target=run_svod_acquisition, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'SVOD Acquisition IQ job submitted successfully',
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
        
        # Check access
        role = user.get('role', 'user')
        if role != 'admin' and role != 'enterprise' and not user.get('has_attribution_iq_access', False):
            return jsonify({'error': 'Attribution IQ access required'}), 403
        
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
        
        # Create job
        job_id = str(uuid.uuid4())
        username = user.get('username', 'unknown')
        
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
        
        # Start job in background thread
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
        
        # Import the script module
        script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'SVOD_Churn_Attribution.py')
        if not os.path.exists(script_path):
            update_job_status(job_id, status='failed', error=f'Script not found: {script_path}')
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
        
        # Build script parameters dict (matching what get_user_input returns)
        script_params = {
            'project_name': params['project_name'],
            'auto_format': True,
            'campaign_start': campaign_start,
            'campaign_end': campaign_end,
            'exclusion_days': int(params['exclusion_days']),
            'attribution_window': int(params['attribution_window']),
            'show_search_terms': params['show_search_terms'],
            'is_new_show': params.get('is_new_show', False),
            'track_episodes': False,  # Simplified - can be enhanced later
            'tracking_mode': None,
            'episode_dates': [],
            'platform_name': params['platform_name'],
            'platform_url_patterns': params['platform_url_patterns']
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
            
            # Call write_output
            if hasattr(module, 'write_output'):
                module.write_output(summary_df, comp_df, demo_df, timing_df, episode_df, monthly_df, episode_timing_df, churn_df, post_signup_touchpoints_df, script_params)
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
        
        # Check access
        role = user.get('role', 'user')
        if role != 'admin' and role != 'enterprise' and not user.get('has_attribution_iq_access', False):
            return jsonify({'error': 'Attribution IQ access required'}), 403
        
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
        username = user.get('username', 'unknown')
        
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
        
        # Check access
        role = user.get('role', 'user')
        if role != 'admin' and role != 'enterprise' and not user.get('has_attribution_iq_access', False):
            return jsonify({'error': 'Attribution IQ access required'}), 403
        
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
        
        # Create job
        job_id = str(uuid.uuid4())
        username = user.get('username', 'unknown')
        
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
        
        # Start job in background thread
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
    app.run(host='0.0.0.0', port=port, debug=debug)
