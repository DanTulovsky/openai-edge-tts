#!/usr/bin/env python3
"""
Integration test for TTS chunking functionality.
Tests actual audio generation with chunked text.
"""

import sys
import os
import tempfile
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

# Set environment variables before importing
os.environ['ENABLE_TEXT_CHUNKING'] = 'true'
os.environ['TEXT_CHUNK_THRESHOLD'] = '500'
os.environ['DEBUG_STREAMING'] = 'false'

from tts_handler import generate_speech, generate_speech_stream


def test_streaming_with_chunking():
    """Test streaming TTS with text chunking."""
    print("Testing streaming TTS with chunking...")

    # Create a text that will be chunked (>500 chars)
    text = """This is the first paragraph of our test. It contains multiple sentences that will help us verify the chunking functionality works correctly.

This is the second paragraph. It also has several sentences to make the text long enough to trigger chunking. We want to ensure that the audio streams back quickly.

This is the third and final paragraph. It completes our test text and should result in multiple chunks being processed sequentially."""

    print(f"  Text length: {len(text)} chars")

    voice = "en-US-AvaNeural"
    speed = 1.0

    # Collect all audio chunks
    audio_chunks = []
    chunk_count = 0

    try:
        for audio_chunk in generate_speech_stream(text, voice, speed):
            chunk_count += 1
            audio_chunks.append(audio_chunk)

        print(f"  Received {chunk_count} audio chunks")

        # Verify we got audio data
        total_audio_size = sum(len(chunk) for chunk in audio_chunks)
        print(f"  Total audio size: {total_audio_size} bytes")

        assert chunk_count > 0, "Should receive at least one audio chunk"
        assert total_audio_size > 0, "Should receive audio data"

        print("✓ Streaming with chunking test passed")
        return True
    except Exception as e:
        print(f"❌ Streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_nonstreaming_with_chunking():
    """Test non-streaming TTS with text chunking and concatenation."""
    print("\nTesting non-streaming TTS with chunking...")

    # Create a text that will be chunked (>500 chars)
    text = """This is a test of the non-streaming mode with text chunking. The text needs to be long enough to trigger the chunking mechanism.

We're adding multiple paragraphs here to ensure we exceed the threshold. Each paragraph adds more content to the overall text length.

This final paragraph should push us well over the 500 character threshold, ensuring that the chunking and concatenation logic is properly tested."""

    print(f"  Text length: {len(text)} chars")

    voice = "en-US-AvaNeural"
    response_format = "mp3"
    speed = 1.0

    try:
        # Generate the audio file
        output_path = generate_speech(text, voice, response_format, speed)

        print(f"  Generated audio file: {output_path}")

        # Verify the file exists and has content
        assert os.path.exists(output_path), f"Output file should exist: {output_path}"

        file_size = os.path.getsize(output_path)
        print(f"  Audio file size: {file_size} bytes")

        assert file_size > 0, "Audio file should have content"

        # Clean up
        Path(output_path).unlink(missing_ok=True)

        print("✓ Non-streaming with chunking test passed")
        return True
    except Exception as e:
        print(f"❌ Non-streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_short_text_no_chunking():
    """Test that short text is not chunked."""
    print("\nTesting short text (no chunking)...")

    text = "This is a short text that should not be chunked."
    print(f"  Text length: {len(text)} chars")

    voice = "en-US-AvaNeural"
    speed = 1.0

    try:
        # Collect audio chunks
        audio_chunks = list(generate_speech_stream(text, voice, speed))

        print(f"  Received {len(audio_chunks)} audio chunks")

        total_audio_size = sum(len(chunk) for chunk in audio_chunks)
        print(f"  Total audio size: {total_audio_size} bytes")

        assert len(audio_chunks) > 0, "Should receive audio chunks"
        assert total_audio_size > 0, "Should receive audio data"

        print("✓ Short text test passed")
        return True
    except Exception as e:
        print(f"❌ Short text test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_chunking_disabled():
    """Test that chunking can be disabled via environment variable."""
    print("\nTesting with chunking disabled...")

    # Temporarily disable chunking
    original_value = os.environ.get('ENABLE_TEXT_CHUNKING')
    os.environ['ENABLE_TEXT_CHUNKING'] = 'false'

    # Reload the module to pick up the new setting
    import importlib
    import tts_handler
    importlib.reload(tts_handler)

    text = """This is a long text that would normally be chunked. But since we've disabled chunking, it should be sent as a single piece to Edge TTS.

We're adding multiple paragraphs to ensure this is long enough to normally trigger chunking.

This should still work fine, just without the chunking optimization."""

    print(f"  Text length: {len(text)} chars")

    voice = "en-US-AvaNeural"
    speed = 1.0

    try:
        # Collect audio chunks
        audio_chunks = list(tts_handler.generate_speech_stream(text, voice, speed))

        print(f"  Received {len(audio_chunks)} audio chunks")

        total_audio_size = sum(len(chunk) for chunk in audio_chunks)
        print(f"  Total audio size: {total_audio_size} bytes")

        assert len(audio_chunks) > 0, "Should receive audio chunks"
        assert total_audio_size > 0, "Should receive audio data"

        print("✓ Chunking disabled test passed")
        return True
    except Exception as e:
        print(f"❌ Chunking disabled test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original value
        if original_value is not None:
            os.environ['ENABLE_TEXT_CHUNKING'] = original_value
        else:
            os.environ.pop('ENABLE_TEXT_CHUNKING', None)

        # Reload again to restore settings
        importlib.reload(tts_handler)


def main():
    """Run all integration tests."""
    print("Running TTS chunking integration tests...\n")
    print("Note: These tests require network access to Edge TTS API\n")

    results = []

    results.append(("Streaming with chunking", test_streaming_with_chunking()))
    results.append(("Non-streaming with chunking", test_nonstreaming_with_chunking()))
    results.append(("Short text (no chunking)", test_short_text_no_chunking()))
    results.append(("Chunking disabled", test_chunking_disabled()))

    print("\n" + "="*60)
    print("Test Results:")
    print("="*60)

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False

    print("="*60)

    if all_passed:
        print("\n✅ All integration tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())




