# Behavioral Graph Web Application

A modern web interface for the BG.py behavioral analysis pipeline. This application provides a user-friendly way to run clickstream analysis queries and download results.

## Features

- 🔐 Password-protected access
- 📊 Submit behavioral graph analysis jobs
- 📈 Real-time job status and progress tracking
- 📥 Download results as CSV
- 🎯 Demographic filtering options
- 🌐 Modern, responsive UI

## Deployment to Render

### Quick Deploy

1. **Push to GitHub:**
   ```bash
   cd bg-webapp
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/bg-webapp.git
   git push -u origin main
   ```

2. **Deploy on Render:**
   - Go to [render.com](https://render.com)
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Render will automatically detect the `render.yaml` and deploy

### Manual Deploy

1. Create a new Web Service on Render
2. Connect your GitHub repository
3. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 300`
4. Add environment variables:
   - `APP_USERNAME`: admin
   - `APP_PASSWORD`: midgenow!2
   - `FLASK_DEBUG`: false

## Local Development

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Copy the BG.py and config.py files:**
   ```bash
   # Make sure bg.py and config.py are in the parent directory
   cp ../bg.py ../
   cp ../config.py ../
   ```

3. **Run the app:**
   ```bash
   python app.py
   ```

4. **Access:** Open http://localhost:5000 in your browser
   - Username: `admin`
   - Password: `midgenow!2`

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_USERNAME` | Login username | `admin` |
| `APP_PASSWORD` | Login password | `midgenow!2` |
| `PORT` | Server port | `5000` |
| `FLASK_DEBUG` | Enable debug mode | `false` |

### Snowflake Configuration

Create a `config.py` file with your Snowflake credentials:

```python
SNOWFLAKE_CONFIG = {
    'user': 'your_username',
    'password': 'your_password',
    'account': 'your_account',
    'warehouse': 'YOUR_WAREHOUSE',
    'database': 'YOUR_DATABASE',
    'schema': 'PUBLIC',
    'role': 'YOUR_ROLE'
}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main application page |
| `/api/health` | GET | Health check (no auth) |
| `/api/submit` | POST | Submit new analysis job |
| `/api/status/<job_id>` | GET | Get job status |
| `/api/download/<job_id>` | GET | Download results |
| `/api/jobs` | GET | List all jobs |
| `/api/me/product-access` | GET | Module flags (dropdown sync) |
| `/api/llmo/summary` | GET | LLMO daily rollup JSON (requires LLMO IQ access) |

### LLMO IQ

Daily summary is built by `build_llmo_summary.py` and uploaded to **`s3://llmo/processed/llmo_daily_summary.json.gz`** (gzip JSON). The web app reads it with the same AWS credentials as other S3 usage — grant **`s3:GetObject`** on that bucket/key (or override with env **`LLMO_S3_BUCKET`** / **`LLMO_SUMMARY_KEY`**). Users need **LLMO IQ** enabled in Admin.

## License

Proprietary - All rights reserved
