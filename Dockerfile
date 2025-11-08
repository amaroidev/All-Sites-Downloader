# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    FC_LANG=C.UTF-8 \
    LANG=C.UTF-8 \
    DOWNLOAD_FOLDER=/app/downloads \
    FLASK_ENV=production

WORKDIR /app

# Install system packages needed by yt-dlp/ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching
COPY requirements.txt ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r requirements.txt

# Copy application source
COPY . .

# Create default download directory and ensure permissions
RUN mkdir -p "$DOWNLOAD_FOLDER" && \
    adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

USER appuser

ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000
VOLUME ["/app/downloads"]

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "4", "--timeout", "120"]
