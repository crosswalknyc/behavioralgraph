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
        persist_s3_cache()
        
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
        persist_s3_cache()
        
        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} file(s)',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        print(f"Delete error: {e}")
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
    """Quick health check endpoint."""
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.now().isoformat(),
        'cache_ready': cache_loading_complete,
        'cache_size': len(s3_cache.get('jobs', []))
    })


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

# Demographics cache - stores pre-computed demographic summaries for each profile
demographics_cache = {}

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

# Try to load persisted cache on startup
if s3_client:
    load_persisted_cache()


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
        
        # Read last 100KB where BRAND CATEGORY row usually is
        start_byte = max(0, file_size - 100000)
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key, Range=f'bytes={start_byte}-{file_size}')
        content = response['Body'].read().decode('utf-8', errors='ignore')
        
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
            from config import SNOWFLAKE_CONFIG
        except ImportError as e:
            update_job_status(job_id, status='failed', error=f'Module import failed: {str(e)}')
            return
        
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
                # Apply frequency analysis if requested (matches bg.py terminal behavior)
                if include_frequency and not is_genpop:
                    try:
                        print("📊 Adding frequency metrics...")
                        update_job_status(job_id, progress=87, message='Adding frequency analysis...')
                        
                        import pandas as pd
                        df = pd.read_csv(result_file)
                        
                        # Run frequency analysis using bg module
                        if hasattr(bg, 'get_frequency_data'):
                            freq_df = bg.get_frequency_data(conn, brands, sample_start, sample_end)
                            if freq_df is not None and not freq_df.empty:
                                # Merge frequency data
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
# STARTUP CACHE LOADING (Fast sync load of persisted cache)
# ============================================================================

import threading
import time as time_module

cache_loading_complete = False
BACKGROUND_CHECK_INTERVAL = 300  # Check for new files every 5 minutes

def quick_startup_cache():
    """Quick startup - just load the pre-built JSON cache (fast!)."""
    global cache_loading_complete
    import time
    start = time.time()
    
    print("🚀 Quick startup cache loading...")
    
    # Load persisted file list cache (single JSON file - should be <1 sec)
    if s3_client:
        try:
            load_persisted_cache()
            print(f"   ✅ Loaded {len(s3_cache.get('jobs', []))} profiles in {time.time()-start:.2f}s")
        except Exception as e:
            print(f"   ⚠️ Cache load error: {e}")
    
    cache_loading_complete = True
    print(f"🎉 Startup complete in {time.time()-start:.2f}s")


def background_cache_checker():
    """Background thread that checks for new/modified files every 5 minutes."""
    print("🔄 Starting background cache checker (every 5 min)...")
    while True:
        time_module.sleep(BACKGROUND_CHECK_INTERVAL)
        try:
            print("🔍 Background check for new files...")
            result = smart_cache_update()
            if result.get('new', 0) > 0 or result.get('updated', 0) > 0:
                print(f"   📥 Found {result.get('new', 0)} new, {result.get('updated', 0)} modified")
        except Exception as e:
            print(f"   ⚠️ Background check error: {e}")


# Load cache at startup (fast - just reads a JSON file)
quick_startup_cache()

# Start background checker thread
bg_checker = threading.Thread(target=background_cache_checker, daemon=True)
bg_checker.start()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
