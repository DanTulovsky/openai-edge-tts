#!/usr/bin/env python3
"""
Manual test script to demonstrate text chunking functionality.
This can be run when Edge TTS API is available.

Usage:
    python test_chunking_manual.py
"""

import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

# Set environment variables to enable chunking with a low threshold for testing
os.environ['ENABLE_TEXT_CHUNKING'] = 'true'
os.environ['TEXT_CHUNK_THRESHOLD'] = '200'  # Low threshold for demo
os.environ['DEBUG_STREAMING'] = 'true'  # Enable debug output

from handle_text import chunk_text_intelligently


def demo_chunking():
    """Demonstrate the text chunking functionality."""
    print("=" * 70)
    print("TEXT CHUNKING DEMONSTRATION")
    print("=" * 70)

    # Example 1: Short text (no chunking)
    print("\n1. SHORT TEXT (< 200 chars) - Should NOT be chunked:")
    print("-" * 70)
    short_text = "This is a short text that should not be chunked."
    print(f"Input length: {len(short_text)} chars")
    chunks = chunk_text_intelligently(short_text, max_chunk_size=200)
    print(f"Number of chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i}: '{chunk}'")

    # Example 2: Medium text with paragraphs
    print("\n2. MEDIUM TEXT (with paragraphs) - Should be chunked by paragraphs:")
    print("-" * 70)
    medium_text = """This is the first paragraph. It has a few sentences to demonstrate chunking.

This is the second paragraph. It also contains multiple sentences to show how the chunking works.

This is the third paragraph. It completes our demonstration."""

    print(f"Input length: {len(medium_text)} chars")
    chunks = chunk_text_intelligently(medium_text, max_chunk_size=200)
    print(f"Number of chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i} ({len(chunk)} chars): '{chunk[:60]}...'")

    # Example 3: Large paragraph (sentence splitting)
    print("\n3. LARGE PARAGRAPH - Should be chunked by sentences:")
    print("-" * 70)
    large_para = ("This is the first sentence in a long paragraph. "
                  "This is the second sentence. "
                  "This is the third sentence. "
                  "This is the fourth sentence. "
                  "This is the fifth sentence. "
                  "This is the sixth sentence. "
                  "This is the seventh sentence.")

    print(f"Input length: {len(large_para)} chars")
    chunks = chunk_text_intelligently(large_para, max_chunk_size=200)
    print(f"Number of chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i} ({len(chunk)} chars): '{chunk[:60]}...'")

    # Example 4: Very large text
    print("\n4. VERY LARGE TEXT (>1000 chars) - Multiple chunks:")
    print("-" * 70)
    very_large = """The implementation of text chunking for TTS streaming represents a significant improvement in user experience. By breaking large texts into smaller segments, we can dramatically reduce the time-to-first-byte for audio playback.

The chunking algorithm uses a smart approach. First, it attempts to split on paragraph boundaries, which are natural breaks in the content. This preserves the semantic structure of the text and ensures that related content stays together.

For paragraphs that exceed the chunk size threshold, the algorithm falls back to sentence-level splitting. This ensures that even very long paragraphs can be processed efficiently without exceeding reasonable chunk sizes.

The system is fully configurable through environment variables. Users can adjust the chunk threshold, enable or disable chunking entirely, and control debug logging to understand how their text is being processed.

This approach provides the best of both worlds: fast streaming for large texts while maintaining audio quality and natural speech patterns. The concatenation of audio chunks is handled seamlessly, ensuring a smooth listening experience."""

    print(f"Input length: {len(very_large)} chars")
    chunks = chunk_text_intelligently(very_large, max_chunk_size=200)
    print(f"Number of chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i} ({len(chunk)} chars): '{chunk[:60]}...'")

    print("\n" + "=" * 70)
    print("DEMONSTRATION COMPLETE")
    print("=" * 70)
    print("\nTo test with actual TTS generation:")
    print("1. Ensure Edge TTS API is accessible")
    print("2. Start the server: python app/server.py")
    print("3. Send a POST request with a large text (>1000 chars)")
    print("4. With DEBUG_STREAMING=true, you'll see chunking in action")
    print("\nExample curl command:")
    print('curl -X POST http://localhost:5050/v1/audio/speech \\')
    print('  -H "Content-Type: application/json" \\')
    print('  -H "Authorization: Bearer your_api_key_here" \\')
    print('  -d \'{"input": "Your large text here...", "voice": "en-US-AvaNeural", "stream_format": "audio_stream"}\' \\')
    print('  --output audio.aac')


if __name__ == "__main__":
    demo_chunking()




