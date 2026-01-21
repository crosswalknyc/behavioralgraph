# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy config example as config
RUN cp config.example.py config.py

# Expose port
EXPOSE 10000

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the application
# Use 1 worker for faster startup, Render will scale if needed
# --access-logfile - logs to stdout
# --error-logfile - logs to stderr
# Removed --preload for faster startup
# Reduced timeout to 60s for faster health check response
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
