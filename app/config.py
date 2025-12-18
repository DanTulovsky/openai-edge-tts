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

    # Text chunking settings
    "TEXT_CHUNK_THRESHOLD": 1000,  # Characters before chunking kicks in
    "ENABLE_TEXT_CHUNKING": True,  # Enable/disable text chunking feature

    # Feature flags
    "REQUIRE_API_KEY": True,
    "REMOVE_FILTER": False,
    "EXPAND_API": True,
    "DETAILED_ERROR_LOGGING": True,
    "DEBUG_STREAMING": False,
}
