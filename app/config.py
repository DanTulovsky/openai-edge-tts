# config.py

import os

# Get version from environment variable (set by Docker build) or use default
VERSION = os.getenv('APP_VERSION', 'dev')

DEFAULT_CONFIGS = {
    # Server settings
    "PORT": 5050,
    "API_KEY": 'your_api_key_here',  # Fallback API key

    # TTS settings
    "DEFAULT_VOICE": 'en-US-AvaNeural',
    "DEFAULT_RESPONSE_FORMAT": 'aac',
    "DEFAULT_SPEED": 1.0,
    "DEFAULT_LANGUAGE": 'en-US',

    # Feature flags
    "REQUIRE_API_KEY": True,
    "REMOVE_FILTER": False,
    "EXPAND_API": True,
    "DETAILED_ERROR_LOGGING": True,
    "DEBUG_STREAMING": False,

    # HLS settings
    "HLS_SEGMENT_DURATION": 4.0,  # Segment duration in seconds (default 5, prefer 3 if technically feasible)
    "HLS_CLEANUP_TIMEOUT": 300,  # Clean up sessions older than this many seconds (5 minutes)
}
