# Base stage with Python and FFmpeg pre-installed
FROM python:3.12-slim AS python-ffmpeg-base

# Install ffmpeg using cache mount for faster rebuilds
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages in base image for layer caching
COPY requirements.txt /tmp/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install opentelemetry-distro opentelemetry-exporter-otlp
RUN --mount=type=cache,target=/root/.cache/pip \
    opentelemetry-bootstrap -a install

# Final stage using the base with ffmpeg
FROM python-ffmpeg-base

ARG VERSION=dev
WORKDIR /app

# Copy the app directory
# Note: Python packages are already installed in the base stage
COPY app/ /app

# Set Python to unbuffered mode for real-time output in Docker
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=${VERSION}

# Command to run the server
CMD ["python", "/app/server.py"]
