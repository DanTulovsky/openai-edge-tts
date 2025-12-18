# handle_text.py

import re
import emoji
from typing import List

def prepare_tts_input_with_context(text: str) -> str:
    """
    Prepares text for a TTS API by cleaning Markdown and adding minimal contextual hints
    for certain Markdown elements like headers. Preserves paragraph separation.

    Args:
        text (str): The raw text containing Markdown or other formatting.

    Returns:
        str: Cleaned text with contextual hints suitable for TTS input.
    """

    # Remove emojis
    text = emoji.replace_emoji(text, replace='')

    # Add context for headers
    def header_replacer(match):
        level = len(match.group(1))  # Number of '#' symbols
        header_text = match.group(2).strip()
        if level == 1:
            return f"Title — {header_text}\n"
        elif level == 2:
            return f"Section — {header_text}\n"
        else:
            return f"Subsection — {header_text}\n"

    text = re.sub(r"^(#{1,6})\s+(.*)", header_replacer, text, flags=re.MULTILINE)

    # Announce links (currently commented out for potential future use)
    # text = re.sub(r"\[([^\]]+)\]\((https?:\/\/[^\)]+)\)", r"\1 (link: \2)", text)

    # Remove links while keeping the link text
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

    # Describe inline code
    text = re.sub(r"`([^`]+)`", r"code snippet: \1", text)

    # Remove bold/italic symbols but keep the content
    text = re.sub(r"(\*\*|__|\*|_)", '', text)

    # Remove code blocks (multi-line) with a description
    text = re.sub(r"```([\s\S]+?)```", r"(code block omitted)", text)

    # Remove image syntax but add alt text if available
    text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"Image: \1", text)

    # Remove HTML tags
    text = re.sub(r"</?[^>]+(>|$)", '', text)

    # Normalize line breaks
    text = re.sub(r"\n{2,}", '\n\n', text)  # Ensure consistent paragraph separation

    # Replace multiple spaces within lines
    text = re.sub(r" {2,}", ' ', text)

    # Trim leading and trailing whitespace from the whole text
    text = text.strip()

    return text


def chunk_text_intelligently(text: str, max_chunk_size: int = 1000) -> List[str]:
    """
    Intelligently splits text into chunks for TTS processing.

    Uses a smart splitting strategy:
    1. First splits on paragraph boundaries (double newlines)
    2. For paragraphs exceeding max_chunk_size, further splits on sentence boundaries
    3. Preserves natural reading flow and whitespace

    Args:
        text (str): The text to chunk
        max_chunk_size (int): Maximum characters per chunk (default 1000)

    Returns:
        List[str]: List of text chunks, each <= max_chunk_size (where possible)
    """
    if len(text) <= max_chunk_size:
        return [text]

    chunks = []

    # Split on paragraph boundaries first
    paragraphs = text.split('\n\n')

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        # Skip empty paragraphs
        if not paragraph:
            continue

        # If paragraph fits within max_chunk_size, add it directly
        if len(paragraph) <= max_chunk_size:
            chunks.append(paragraph)
        else:
            # Paragraph is too large, split on sentence boundaries
            # Use regex to split on sentence endings while preserving the punctuation
            # Handles common abbreviations (Dr., Mr., Mrs., Ms., etc.) to avoid false splits
            sentence_pattern = r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|\!)\s+'
            sentences = re.split(sentence_pattern, paragraph)

            current_chunk = ""
            for sentence in sentences:
                sentence = sentence.strip()

                # Skip empty sentences
                if not sentence:
                    continue

                # If adding this sentence would exceed max_chunk_size
                if current_chunk and len(current_chunk) + len(sentence) + 1 > max_chunk_size:
                    # Save current chunk and start a new one
                    chunks.append(current_chunk.strip())
                    current_chunk = sentence
                else:
                    # Add sentence to current chunk
                    if current_chunk:
                        current_chunk += " " + sentence
                    else:
                        current_chunk = sentence

            # Add any remaining content
            if current_chunk:
                chunks.append(current_chunk.strip())

    # Filter out any empty chunks
    chunks = [chunk for chunk in chunks if chunk.strip()]

    return chunks if chunks else [text]  # Fallback to original text if chunking fails
