FROM python:3.12-slim

# ffmpeg  — required by yt-dlp to merge streams and embed metadata
# Playwright/Chromium system deps are installed by `playwright install --with-deps`
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium (used by TikTokApi) plus all OS-level dependencies it needs
RUN playwright install chromium --with-deps

COPY . .

# Persistent volumes are mounted here at runtime
RUN mkdir -p /app/data /app/videos

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    VIDEOS_DIR=/app/videos \
    WEB_PORT=5000

EXPOSE 5000

CMD ["python", "main.py"]
