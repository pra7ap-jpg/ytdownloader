# Dockerfile to build a Python Telegram bot with ffmpeg and yt-dlp
FROM python:3.10-slim

# Install system dependencies: ffmpeg is crucial for combining video/audio streams.
# We use a multi-line RUN command with backslashes for clean readability and correct syntax.
RUN apt-get update && \
    apt-get install -y ffmpeg --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY youtube_bot.py .

# Expose the port the app will run on (Render sets the PORT environment variable)
EXPOSE 8080

# Command to run the application using Gunicorn with gevent workers for async support
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --worker-class gevent --worker-connections 1000 youtube_bot:app_flask
