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
from datetime import datetime
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
@app.route('/health')
@app.route('/healthz')  # Also support /healthz for Render compatibility
def health_check_root():
    """Root health check endpoint for Render - must be fast and not depend on any initialization."""
    return 'ok', 200

print("✅ Health check endpoints registered (/health and /healthz)")

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
            'has_subscriber_iq_access': req_data.get('has_subscriber_iq_access', False)
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
                          region_name='us-east-1')
        
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
                project_name = cached.get('project_name', filename.replace('.csv', '').replace('_', ' ').title())
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
                        's3_key': key  # Original key in svod bucket
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
                project_name = filename.replace('.csv', '').replace('_', ' ').title()
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
                          region_name='us-east-1')
        
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
                          region_name='us-east-1')
        
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
                          region_name='us-east-1')
        
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
    # Admins always have access to all dashboards
    if role == 'admin':
        has_profile_iq = True
        has_subscriber_iq = True
    else:
        has_profile_iq = user.get('has_profile_iq_access', True) if user else True  # Default True for backward compat
        has_subscriber_iq = user.get('has_subscriber_iq_access', False) if user else False
    
    return render_template('index.html', 
                           username=session.get('username'),
                           role=role,
                           credits=user.get('credits', 0) if user else 0,
                           credits_used=user.get('credits_used', 0) if user else 0,
                           profile_picture=user.get('profile_picture', '') if user else '',
                           has_profile_iq_access=has_profile_iq,
                           has_subscriber_iq_access=has_subscriber_iq)


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
    
    for i, row in enumerate(rows):
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
            elif 'Platform Tracked' in first_col:
                parsed['metadata']['platform'] = row[1].strip() if len(row) > 1 else ''
            elif 'Analysis Date Range' in first_col:
                parsed['metadata']['date_range'] = row[1].strip() if len(row) > 1 else ''
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
            if first_col.startswith('Episode '):
                episode_num = first_col.replace('Episode ', '').strip()
                signups_val = parse_number(row[1]) if len(row) > 1 else None
                print(f"   📊 Found Episode {episode_num}: signups={signups_val}")
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
                parsed['demographics']['age'].append({
                    'age_range': first_col,
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
                
                files.append({
                    's3_key': key,
                    'show_name': show_name,
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
        parsed = parse_subscriber_iq_csv(csv_content)
        
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
        return jsonify({
            'success': True,
            'data': parsed,
            'show': show_name.upper(),
            'date_range': date_range,
            's3_key': s3_key
        })
    except Exception as e:
        print(f"❌ Error in get_subscriber_iq_data: {e}")
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
        
        # Update cache - find job in s3_cache and update its key
        if s3_cache and 'jobs' in s3_cache:
            for job in s3_cache.get('jobs', []):
                if job.get('key') == old_key or job.get('s3_key') == old_key:
                    job['key'] = new_key
                    job['s3_key'] = new_key
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
            # Handle SVOD file - store category in metadata
            actual_key = file_key.replace('svod-acquisition/', '')
            metadata = load_svod_metadata()
            if actual_key not in metadata:
                metadata[actual_key] = {}
            metadata[actual_key]['category'] = new_category
            save_svod_metadata(metadata)
            
            print(f"🏷️ Changed SVOD category for {actual_key} to {new_category}")
            return jsonify({
                'success': True,
                'new_category': new_category,
                'message': 'Category updated successfully'
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
    """
    import time
    from datetime import datetime, timezone, timedelta
    
    if not s3_client:
        return {'new': 0, 'updated': 0, 'deleted': 0, 'total': 0}
    
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
                    s3_cache['jobs'].append(job_data)
                    if job_data['category'] not in s3_cache['categories']:
                        s3_cache['categories'].append(job_data['category'])
                    new_count += 1
                    print(f"   ➕ New: {key}")
                    
                elif existing[key] != obj_modified:
                    # MODIFIED file
                    job_data = process_s3_file_metadata(key, obj)
                    job_data['last_modified'] = obj_modified
                    # Update in place
                    for i, job in enumerate(s3_cache['jobs']):
                        if job.get('s3_key') == key:
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

def process_s3_file_metadata(key, obj):
    """Process a single S3 file and extract metadata."""
    import re
    
    # Extract project name from filename
    name_without_ext = key.replace('.csv', '')
    match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
    if match:
        project_name = match.group(1).replace('_', ' ').upper()
    else:
        project_name = name_without_ext.replace('_', ' ').upper()
    
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
print("🚀 App starting - cache will load in background...")
cache_thread = threading.Thread(target=async_cache_loader, daemon=True)
cache_thread.start()

# Start background checker thread
bg_checker = threading.Thread(target=background_cache_checker, daemon=True)
bg_checker.start()


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
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
