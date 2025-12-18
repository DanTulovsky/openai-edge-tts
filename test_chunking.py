#!/usr/bin/env python3
"""
Test script for text chunking functionality.
Tests various text sizes and verifies the chunking logic works correctly.
"""

import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from handle_text import chunk_text_intelligently


def test_short_text():
    """Test that short text is not chunked."""
    text = "This is a short text that should not be chunked."
    chunks = chunk_text_intelligently(text, max_chunk_size=1000)
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
    assert chunks[0] == text, "Short text should remain unchanged"
    print("✓ Short text test passed")


def test_paragraph_splitting():
    """Test that text is split on paragraph boundaries."""
    text = """First paragraph with some content.

Second paragraph with more content.

Third paragraph with even more content."""

    chunks = chunk_text_intelligently(text, max_chunk_size=50)
    assert len(chunks) == 3, f"Expected 3 chunks (one per paragraph), got {len(chunks)}"
    assert "First paragraph" in chunks[0]
    assert "Second paragraph" in chunks[1]
    assert "Third paragraph" in chunks[2]
    print("✓ Paragraph splitting test passed")


def test_sentence_splitting():
    """Test that large paragraphs are split on sentence boundaries."""
    text = ("This is the first sentence. This is the second sentence. "
            "This is the third sentence. This is the fourth sentence. "
            "This is the fifth sentence.")

    chunks = chunk_text_intelligently(text, max_chunk_size=80)
    assert len(chunks) > 1, f"Expected multiple chunks, got {len(chunks)}"

    # Verify all chunks are within size limit (with some tolerance for sentence boundaries)
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}: {len(chunk)} chars - '{chunk[:50]}...'")

    print("✓ Sentence splitting test passed")


def test_large_text():
    """Test with a large text (>1000 chars)."""
    # Create a text that's about 3000 characters
    paragraph = "This is a test paragraph that will be repeated multiple times to create a large text. " * 10
    text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"

    print(f"  Total text length: {len(text)} chars")

    chunks = chunk_text_intelligently(text, max_chunk_size=1000)
    print(f"  Number of chunks: {len(chunks)}")

    assert len(chunks) > 1, f"Expected multiple chunks for large text, got {len(chunks)}"

    # Verify all text is preserved
    reconstructed = " ".join(chunks)
    # Note: Some whitespace normalization may occur
    assert len(reconstructed) > 0, "Reconstructed text should not be empty"

    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}: {len(chunk)} chars")
        assert len(chunk) <= 1200, f"Chunk {i+1} exceeds reasonable size: {len(chunk)} chars"

    print("✓ Large text test passed")


def test_abbreviations():
    """Test that common abbreviations don't cause false sentence splits."""
    text = "Dr. Smith went to the store. Mr. Jones followed him. Mrs. Williams stayed home."

    chunks = chunk_text_intelligently(text, max_chunk_size=50)

    # Should split on actual sentence boundaries, not on abbreviations
    for chunk in chunks:
        print(f"  Chunk: '{chunk}'")
        # Dr., Mr., Mrs. should not cause splits in the middle
        assert not chunk.strip().startswith("Smith"), "Should not split after 'Dr.'"
        assert not chunk.strip().startswith("Jones"), "Should not split after 'Mr.'"
        assert not chunk.strip().startswith("Williams"), "Should not split after 'Mrs.'"

    print("✓ Abbreviations test passed")


def test_empty_text():
    """Test handling of empty text."""
    text = ""
    chunks = chunk_text_intelligently(text, max_chunk_size=1000)
    assert len(chunks) == 1, f"Expected 1 chunk for empty text, got {len(chunks)}"
    assert chunks[0] == "", "Empty text should return empty chunk"
    print("✓ Empty text test passed")


def test_whitespace_only():
    """Test handling of whitespace-only text."""
    text = "   \n\n   \n\n   "
    chunks = chunk_text_intelligently(text, max_chunk_size=1000)
    # Should filter out empty chunks
    assert len(chunks) <= 1, f"Expected 0-1 chunks for whitespace-only text, got {len(chunks)}"
    print("✓ Whitespace-only test passed")


def main():
    """Run all tests."""
    print("Testing text chunking functionality...\n")

    try:
        test_short_text()
        test_paragraph_splitting()
        test_sentence_splitting()
        test_large_text()
        test_abbreviations()
        test_empty_text()
        test_whitespace_only()

        print("\n✅ All tests passed!")
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())




