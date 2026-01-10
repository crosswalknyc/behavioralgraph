#!/usr/bin/env python3
"""
Behavioral Graph Web Application
================================
A Flask-based web interface for the BG.py behavioral analysis pipeline.
Password protected with basic authentication.
"""

import os
import sys
import uuid
import json
import threading
import traceback
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response
from flask_cors import CORS
import pandas as pd

# Add parent directory to path for importing bg module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)
CORS(app)

# ============================================================================
# AUTHENTICATION
# ============================================================================

# Credentials
USERNAME = os.environ.get('APP_USERNAME', 'admin')
PASSWORD = os.environ.get('APP_PASSWORD', 'midgenow!2')

def check_auth(username, password):
    """Check if username/password combination is valid."""
    return username == USERNAME and password == PASSWORD

def authenticate():
    """Send a 401 response that enables basic auth."""
    return Response(
        'Access denied. Please provide valid credentials.',
        401,
        {'WWW-Authenticate': 'Basic realm="Behavioral Graph Access"'}
    )

def requires_auth(f):
    """Decorator to require authentication for a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# Store for job status and results
jobs = {}
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
@requires_auth
def index():
    """Render the main application page."""
    return render_template('index.html')


@app.route('/api/health')
def health_check():
    """Health check endpoint for Render (no auth required)."""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})


@app.route('/api/submit', methods=['POST'])
@requires_auth
def submit_analysis():
    """Submit a new behavioral graph analysis job."""
    try:
        data = request.json
        
        # Validate required fields
        required = ['project_name', 'brands', 'start_date', 'end_date']
        for field in required:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Create job ID
        job_id = str(uuid.uuid4())[:8]
        
        # Parse inputs
        import re
        project_name = data['project_name'].replace(' ', '_')
        project_name = re.sub(r'[<>:"/\\|?*]', '_', project_name)
        
        # Parse brands (handle comma-separated and newline-separated)
        brands_raw = data['brands'].replace('\n', ',')
        brands = []
        for b in brands_raw.split(','):
            b = b.strip()
            if not b:
                continue
            # Extract domain from URLs
            match = re.search(r'https?://([^/]+)', b)
            clean_brand = match.group(1).lower() if match else b.lower()
            brands.append(clean_brand)
        
        # Always auto-format brand variations
        expanded_brands = []
        for brand in brands:
            expanded_brands.append(brand)
            # Add common variations
            if '.' in brand:
                expanded_brands.append(brand.replace('.', ''))
            if ' ' in brand:
                expanded_brands.append(brand.replace(' ', ''))
                expanded_brands.append(brand.replace(' ', '-'))
        brands = list(set(expanded_brands))
        
        # Parse dates
        try:
            start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').strftime('%Y-%m-%d')
            end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').strftime('%Y-%m-%d')
            behavior_start = datetime.strptime(data.get('behavior_start', data['start_date']), '%Y-%m-%d').strftime('%Y-%m-%d')
            behavior_end = datetime.strptime(data.get('behavior_end', data['end_date']), '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        # Optional parameters
        is_genpop = data.get('is_genpop', True)
        purchasers_only = data.get('purchasers_only', False)
        brand_category = data.get('brand_category', 'GENERAL')
        include_frequency = data.get('include_frequency', False)
        is_listener_watcher = data.get('is_listener_watcher', False)
        previous_file = data.get('previous_file', None) if data.get('is_update', False) else None
        
        # Demographic filters
        filters = {}
        if data.get('gender'):
            filters['GENDER'] = [data['gender']]
        if data.get('age'):
            filters['AGE'] = [data['age']]
        if data.get('ethnicity'):
            filters['ETHNICITY'] = [data['ethnicity']]
        if data.get('income'):
            filters['INCOME'] = [data['income']]
        if data.get('education'):
            filters['EDUCATION'] = [data['education']]
        if data.get('relationship'):
            filters['RELATIONSHIP'] = [data['relationship']]
        if data.get('sexual_orientation'):
            filters['SEXUAL_ORIENTATION'] = [data['sexual_orientation']]
        if data.get('parental_status'):
            filters['PARENTAL_STATUS'] = [data['parental_status']]
        
        # Skew settings for demographic safety checks
        skew_settings = {}
        if data.get('enable_skew', False) and data.get('skew_category') and data.get('skew_target'):
            targets = [t.strip() for t in data['skew_target'].split(',')]
            skew_settings[data['skew_category']] = {
                'target': targets,
                'strength': data.get('skew_strength', 'medium')
            }
        
        # Initialize job
        jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'message': 'Job queued...',
            'created_at': datetime.now().isoformat(),
            'project_name': project_name,
            'result_file': None,
            'error': None,
            'logs': []
        }
        
        # Start processing in background thread
        thread = threading.Thread(
            target=run_analysis,
            args=(job_id, project_name, brands, start_date, end_date, 
                  behavior_start, behavior_end, filters, skew_settings, 
                  is_genpop, purchasers_only, brand_category, 
                  include_frequency, is_listener_watcher, previous_file)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'job_id': job_id,
            'message': 'Analysis job submitted successfully',
            'status': 'queued',
            'brands_count': len(brands)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status/<job_id>')
@requires_auth
def get_job_status(job_id):
    """Get the status of a specific job."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify({
        'job_id': job_id,
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message'],
        'created_at': job['created_at'],
        'error': job['error'],
        'logs': job['logs'][-20:],  # Last 20 log entries
        'result_file': job['result_file']
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


@app.route('/api/jobs')
@requires_auth
def list_jobs():
    """List all jobs (limited info)."""
    job_list = []
    for job_id, job in jobs.items():
        job_list.append({
            'job_id': job_id,
            'project_name': job['project_name'],
            'status': job['status'],
            'progress': job['progress'],
            'created_at': job['created_at']
        })
    return jsonify(sorted(job_list, key=lambda x: x['created_at'], reverse=True))


# ============================================================================
# ANALYSIS RUNNER
# ============================================================================

def update_job_status(job_id, status=None, progress=None, message=None, error=None, result_file=None):
    """Update job status in the jobs dictionary."""
    if job_id in jobs:
        if status:
            jobs[job_id]['status'] = status
        if progress is not None:
            jobs[job_id]['progress'] = progress
        if message:
            jobs[job_id]['message'] = message
            jobs[job_id]['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        if error:
            jobs[job_id]['error'] = error
        if result_file:
            jobs[job_id]['result_file'] = result_file


def run_analysis(job_id, project_name, brands, sample_start, sample_end, 
                 behavior_start, behavior_end, filters, skew_settings, 
                 is_genpop, purchasers_only, brand_category,
                 include_frequency=False, is_listener_watcher=False, previous_file_path=None):
    """Run the behavioral graph analysis pipeline."""
    try:
        update_job_status(job_id, status='running', progress=5, message='Starting analysis...')
        
        # Import the bg module
        update_job_status(job_id, progress=10, message='Loading BG pipeline...')
        
        try:
            import bg
            from config import SNOWFLAKE_CONFIG
        except ImportError as e:
            update_job_status(job_id, status='failed', error=f'Failed to import BG module: {str(e)}')
            return
        
        # Connect to Snowflake
        update_job_status(job_id, progress=15, message='Connecting to Snowflake...')
        
        try:
            conn = bg.connect_snowflake()
        except Exception as e:
            update_job_status(job_id, status='failed', error=f'Snowflake connection failed: {str(e)}')
            return
        
        update_job_status(job_id, progress=20, message=f'Connected! Analyzing {len(brands)} brands...')
        
        # Log the parameters being used
        update_job_status(job_id, progress=25, message=f'Sample period: {sample_start} to {sample_end}')
        update_job_status(job_id, progress=30, message=f'Behavior period: {behavior_start} to {behavior_end}')
        if filters:
            update_job_status(job_id, progress=32, message=f'Filters: {list(filters.keys())}')
        
        # Run the full pipeline
        try:
            update_job_status(job_id, progress=35, message='Running full universe scan...')
            
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
            
            update_job_status(job_id, progress=90, message='Processing complete, saving results...')
            
            # Copy result to our outputs directory
            if result_file and os.path.exists(result_file):
                output_filename = f"{job_id}_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                output_path = os.path.join(OUTPUT_DIR, output_filename)
                
                # Copy the file
                import shutil
                shutil.copy2(result_file, output_path)
                
                update_job_status(
                    job_id, 
                    status='completed', 
                    progress=100, 
                    message=f'Analysis completed! Output: {output_filename}',
                    result_file=output_path
                )
            else:
                update_job_status(job_id, status='failed', error='Pipeline completed but no output file generated')
                
        except Exception as e:
            error_msg = f'Pipeline error: {str(e)}\n{traceback.format_exc()}'
            update_job_status(job_id, status='failed', error=error_msg)
            
        finally:
            # Close connection
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        error_msg = f'Unexpected error: {str(e)}\n{traceback.format_exc()}'
        update_job_status(job_id, status='failed', error=error_msg)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
