#!/bin/bash

# Startup script for OpenAI Edge TTS server with maximum debugging enabled

set -e

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Set environment variables for maximum debugging
export DEBUG_STREAMING=True
export DETAILED_ERROR_LOGGING=True
export PORT=7777

# Optional: Run in production-style mode to use gevent WSGI server for proper chunked streaming
# Set FLASK_DEBUG=0 so server.py uses gevent WSGIServer instead of Flask dev server
export FLASK_DEBUG=0
export FLASK_ENV=production

# Print configuration
echo "=========================================="
echo "Starting OpenAI Edge TTS Server"
echo "=========================================="
echo "Debugging Configuration:"
echo "  DEBUG_STREAMING=$DEBUG_STREAMING"
echo "  DETAILED_ERROR_LOGGING=$DETAILED_ERROR_LOGGING"
echo "  FLASK_DEBUG=$FLASK_DEBUG"
echo "  PORT=$PORT"
echo "=========================================="
echo ""

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "=========================================="
    echo "Shutting down server..."
    echo "=========================================="
    # Kill any background processes
    jobs -p | xargs -r kill 2>/dev/null || true
    exit 0
}

# Set trap to catch SIGINT (Ctrl+C) and SIGTERM
trap cleanup SIGINT SIGTERM

# Start the server
python app/server.py

# If we reach here, the server exited normally
cleanup

