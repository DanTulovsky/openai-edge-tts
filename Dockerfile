# Base stage with Python and FFmpeg pre-installed
FROM python:3.12-slim AS python-ffmpeg-base

# Corporate TLS inspection / custom MITM CA: PyPI appears as "self-signed in chain".
# Option A (simple): build with --build-arg PIP_USE_TRUSTED_PYPI=true
# Option B (advanced): --build-arg PIP_TRUSTOPTS="--trusted-host pypi.org --trusted-host files.pythonhosted.org"
# Option C: COPY your PEM as e.g. corporate-ca.crt next to Dockerfile, then:
#   RUN cp corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt \
#       && update-ca-certificates
# (preferred over trusted-host when you have the inspecting CA PEM.)
ARG PIP_USE_TRUSTED_PYPI=false
ARG PIP_TRUSTOPTS=

RUN if [ "$PIP_USE_TRUSTED_PYPI" = "true" ]; then \
      mkdir -p /root/.config/pip && \
      printf '%s\n' \
        '[global]' \
        'trusted-host = pypi.org files.pythonhosted.org' \
        > /root/.config/pip/pip.conf; \
    fi

# Install ffmpeg using cache mount for faster rebuilds (ca-certificates: OS trust store)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates ffmpeg && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages in base image for layer caching
COPY requirements.txt /tmp/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ${PIP_TRUSTOPTS} -r /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ${PIP_TRUSTOPTS} opentelemetry-distro opentelemetry-exporter-otlp
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
