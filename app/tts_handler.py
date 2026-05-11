# tts_handler.py

import edge_tts
import asyncio
import html
import re
import tempfile
import subprocess
import os
from pathlib import Path
from datetime import datetime
import aiohttp

from utils import DETAILED_ERROR_LOGGING, DEBUG_STREAMING, getenv_bool
from config import DEFAULT_CONFIGS
from handle_text import chunk_text_intelligently

# Language default (environment variable)
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', DEFAULT_CONFIGS["DEFAULT_LANGUAGE"])

# Text chunking settings
TEXT_CHUNK_THRESHOLD = int(os.getenv('TEXT_CHUNK_THRESHOLD', str(DEFAULT_CONFIGS["TEXT_CHUNK_THRESHOLD"])))
ENABLE_TEXT_CHUNKING = getenv_bool('ENABLE_TEXT_CHUNKING', DEFAULT_CONFIGS["ENABLE_TEXT_CHUNKING"])

# OpenAI voice names mapped to edge-tts equivalents
voice_mapping = {
    'alloy': 'en-US-JennyNeural',
    'ash': 'en-US-AndrewNeural',
    'ballad': 'en-GB-ThomasNeural',
    'coral': 'en-AU-NatashaNeural',
    'echo': 'en-US-GuyNeural',
    'fable': 'en-GB-SoniaNeural',
    'nova': 'en-US-AriaNeural',
    'onyx': 'en-US-EricNeural',
    'sage': 'en-US-JennyNeural',
    'shimmer': 'en-US-EmmaNeural',
    'verse': 'en-US-BrianNeural',
}

model_data = [
    {"id": "tts-1", "name": "Text-to-speech v1"},
    {"id": "tts-1-hd", "name": "Text-to-speech v1 HD"},
    {"id": "gpt-4o-mini-tts", "name": "GPT-4o mini TTS"}
]


def is_ffmpeg_installed():
    """Check if FFmpeg is installed and accessible."""
    try:
        subprocess.run(['ffmpeg', '-version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def prepare_edge_tts_input(edge_tts_voice: str, text: str) -> str:
    """Multilingual neural voices auto-detect language, which caused alternating EN/IT audio (WET-163).

    When the voice short name is locale-prefixed (e.g. it-IT-*Multilingual*), wrap plain text in SSML
    with ``xml:lang`` so Edge uses a fixed language. Caller SSML (``<speak``) is left unchanged.
    """
    if not text or not text.strip():
        return text
    stripped = text.lstrip()
    if stripped.startswith("<speak"):
        return text
    if "Multilingual" not in edge_tts_voice:
        return text
    m = re.match(r"^([a-z]{2})-([A-Z]{2})-", edge_tts_voice)
    if not m:
        return text
    locale = f"{m.group(1)}-{m.group(2)}"
    inner = html.escape(text, quote=False)
    vname = html.escape(edge_tts_voice, quote=True)
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{locale}"><voice name="{vname}">{inner}</voice></speak>'
    )


async def _generate_audio_stream(text, voice, speed):
    """Generate streaming TTS audio using edge-tts."""
    if DEBUG_STREAMING:
        start_time = datetime.now()
        print(f"[DEBUG_STREAMING] _generate_audio_stream: Entry - text_length={len(text)}, voice={voice}, speed={speed}, timestamp={start_time}")

    # Determine if the voice is an OpenAI-compatible voice or a direct edge-tts voice
    edge_tts_voice = voice_mapping.get(voice, voice)  # Use mapping if in OpenAI names, otherwise use as-is
    text = prepare_edge_tts_input(edge_tts_voice, text)

    # Convert speed to SSML rate format
    try:
        speed_rate = speed_to_rate(speed)  # Convert speed value to "+X%" or "-X%"
    except Exception as e:
        print(f"Error converting speed: {e}. Defaulting to +0%.")
        speed_rate = "+0%"

    # Create the communicator for streaming
    if DEBUG_STREAMING:
        comm_create_start = datetime.now()
        print(f"[DEBUG_STREAMING] _generate_audio_stream: Creating communicator - timestamp={comm_create_start}")

    # Force a fresh aiohttp connector to prevent connection pooling issues
    connector = aiohttp.TCPConnector(force_close=True)
    communicator = edge_tts.Communicate(text=text, voice=edge_tts_voice, rate=speed_rate, connector=connector)

    if DEBUG_STREAMING:
        comm_create_end = datetime.now()
        comm_create_delta = (comm_create_end - comm_create_start).total_seconds()
        print(f"[DEBUG_STREAMING] _generate_audio_stream: Communicator created - took={comm_create_delta:.3f}s, timestamp={comm_create_end}")

    # Stream the audio data
    if DEBUG_STREAMING:
        stream_entry_time = datetime.now()
        stream_delta = (stream_entry_time - comm_create_end).total_seconds()
        print(f"[DEBUG_STREAMING] _generate_audio_stream: Entering stream() loop - timestamp={stream_entry_time}, delta_from_comm_create={stream_delta:.3f}s")

    chunk_count = 0
    audio_chunk_count = 0
    first_chunk_time = None
    last_chunk_time = None

    async for chunk in communicator.stream():
        chunk_received_time = datetime.now()
        chunk_type = chunk.get("type", "unknown")
        chunk_size = len(chunk.get("data", b""))

        if DEBUG_STREAMING:
            if chunk_count == 0:
                first_chunk_delta = (chunk_received_time - stream_entry_time).total_seconds()
                print(f"[DEBUG_STREAMING] _generate_audio_stream: First chunk received - type={chunk_type}, size={chunk_size} bytes, timestamp={chunk_received_time}, delta_from_stream_entry={first_chunk_delta:.3f}s")
                first_chunk_time = chunk_received_time
            else:
                if last_chunk_time:
                    chunk_delta = (chunk_received_time - last_chunk_time).total_seconds()
                    print(f"[DEBUG_STREAMING] _generate_audio_stream: Chunk received - type={chunk_type}, size={chunk_size} bytes, chunk_num={chunk_count}, timestamp={chunk_received_time}, delta_from_last_chunk={chunk_delta:.3f}s")
                else:
                    print(f"[DEBUG_STREAMING] _generate_audio_stream: Chunk received - type={chunk_type}, size={chunk_size} bytes, chunk_num={chunk_count}, timestamp={chunk_received_time}")

        chunk_count += 1
        last_chunk_time = chunk_received_time

        if chunk["type"] == "audio":
            audio_chunk_count += 1
            if DEBUG_STREAMING:
                yield_time = datetime.now()
                yield_delta = (yield_time - chunk_received_time).total_seconds()
                print(f"[DEBUG_STREAMING] _generate_audio_stream: Yielding audio chunk - chunk_num={audio_chunk_count}, size={chunk_size} bytes, timestamp={yield_time}, delta_from_receive={yield_delta:.3f}s")
            yield chunk["data"]

    if DEBUG_STREAMING:
        end_time = datetime.now()
        total_delta = (end_time - start_time).total_seconds()
        if first_chunk_time:
            first_chunk_delta = (first_chunk_time - comm_create_start).total_seconds()
            print(f"[DEBUG_STREAMING] _generate_audio_stream: Completed - total_chunks={chunk_count}, audio_chunks={audio_chunk_count}, total_time={total_delta:.3f}s, time_to_first_chunk={first_chunk_delta:.3f}s, timestamp={end_time}")
        else:
            print(f"[DEBUG_STREAMING] _generate_audio_stream: Completed - total_chunks={chunk_count}, audio_chunks={audio_chunk_count}, total_time={total_delta:.3f}s, timestamp={end_time}")


def generate_speech_stream(text, voice, speed=1.0):
    """Generate streaming speech audio (synchronous wrapper) with intelligent text chunking."""
    if DEBUG_STREAMING:
        start_time = datetime.now()
        print(f"[DEBUG_STREAMING] generate_speech_stream: Entry - text_length={len(text)}, timestamp={start_time}")

    # For short texts, use original logic (no chunking overhead)
    should_chunk = ENABLE_TEXT_CHUNKING and len(text) > TEXT_CHUNK_THRESHOLD

    if not should_chunk:
        # Use original simple logic for non-chunked texts (exactly as it was before)
        if DEBUG_STREAMING:
            loop_create_start = datetime.now()
            print(f"[DEBUG_STREAMING] generate_speech_stream: Creating event loop - timestamp={loop_create_start}")

        # Create and set the event loop FIRST, before creating the async generator
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if DEBUG_STREAMING:
            loop_create_end = datetime.now()
            loop_create_delta = (loop_create_end - loop_create_start).total_seconds()
            print(f"[DEBUG_STREAMING] generate_speech_stream: Event loop created and set - took={loop_create_delta:.3f}s, timestamp={loop_create_end}")

        # Now create the async generator WITHIN the proper event loop context
        async_generator = _generate_audio_stream(text, voice, speed)

        try:
            chunk_count = 0
            last_chunk_time = None

            while True:
                try:
                    if DEBUG_STREAMING:
                        retrieve_start = datetime.now()
                        if chunk_count == 0:
                            print(f"[DEBUG_STREAMING] generate_speech_stream: Retrieving first chunk from async generator - timestamp={retrieve_start}")
                        else:
                            if last_chunk_time:
                                retrieve_delta = (retrieve_start - last_chunk_time).total_seconds()
                                print(f"[DEBUG_STREAMING] generate_speech_stream: Retrieving chunk {chunk_count + 1} from async generator - timestamp={retrieve_start}, delta_from_last_yield={retrieve_delta:.3f}s")

                    next_chunk = loop.run_until_complete(async_generator.__anext__())

                    if DEBUG_STREAMING:
                        retrieve_end = datetime.now()
                        retrieve_delta = (retrieve_end - retrieve_start).total_seconds()
                        chunk_size = len(next_chunk)
                        print(f"[DEBUG_STREAMING] generate_speech_stream: Retrieved chunk - chunk_num={chunk_count + 1}, size={chunk_size} bytes, retrieval_took={retrieve_delta:.3f}s, timestamp={retrieve_end}")
                except StopAsyncIteration:
                    break

                chunk_count += 1

                if DEBUG_STREAMING:
                    yield_time = datetime.now()
                    if last_chunk_time:
                        yield_delta = (yield_time - last_chunk_time).total_seconds()
                        print(f"[DEBUG_STREAMING] generate_speech_stream: Yielding chunk - chunk_num={chunk_count}, size={len(next_chunk)} bytes, timestamp={yield_time}, delta_from_last_yield={yield_delta:.3f}s")
                    else:
                        print(f"[DEBUG_STREAMING] generate_speech_stream: Yielding chunk - chunk_num={chunk_count}, size={len(next_chunk)} bytes, timestamp={yield_time}")

                yield next_chunk
                last_chunk_time = datetime.now() if DEBUG_STREAMING else None
        finally:
            # Best-effort cleanup of async generators and loop
            if DEBUG_STREAMING:
                cleanup_start = datetime.now()
                print(f"[DEBUG_STREAMING] generate_speech_stream: Starting cleanup - timestamp={cleanup_start}")

            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

            if DEBUG_STREAMING:
                cleanup_end = datetime.now()
                cleanup_delta = (cleanup_end - cleanup_start).total_seconds()
                total_delta = (cleanup_end - start_time).total_seconds()
                print(f"[DEBUG_STREAMING] generate_speech_stream: Cleanup completed - cleanup_took={cleanup_delta:.3f}s, total_time={total_delta:.3f}s, total_chunks={chunk_count}, timestamp={cleanup_end}")
        return

    # Large texts - chunk and process sequentially
    text_chunks = chunk_text_intelligently(text, TEXT_CHUNK_THRESHOLD)
    # Filter out empty/whitespace chunks
    text_chunks = [chunk for chunk in text_chunks if chunk and chunk.strip()]

    if DEBUG_STREAMING:
        print(f"[DEBUG_STREAMING] generate_speech_stream: Text chunked into {len(text_chunks)} chunks - chunk_sizes={[len(c) for c in text_chunks]}")

    # If all chunks were filtered out, return early
    if not text_chunks:
        if DEBUG_STREAMING:
            print(f"[DEBUG_STREAMING] generate_speech_stream: All chunks filtered out, returning early")
        return

    # Process each text chunk sequentially
    total_audio_chunks = 0
    for text_chunk_idx, text_chunk in enumerate(text_chunks):
        # Skip empty or whitespace-only chunks
        if not text_chunk or not text_chunk.strip():
            if DEBUG_STREAMING:
                print(f"[DEBUG_STREAMING] generate_speech_stream: Skipping empty/whitespace chunk {text_chunk_idx + 1}/{len(text_chunks)}")
            continue

        if DEBUG_STREAMING:
            chunk_start_time = datetime.now()
            print(f"[DEBUG_STREAMING] generate_speech_stream: Processing text chunk {text_chunk_idx + 1}/{len(text_chunks)} - length={len(text_chunk)}, timestamp={chunk_start_time}")

        if DEBUG_STREAMING:
            loop_create_start = datetime.now()
            print(f"[DEBUG_STREAMING] generate_speech_stream: Creating event loop for chunk {text_chunk_idx + 1} - timestamp={loop_create_start}")

        # Create and set the event loop for this chunk
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if DEBUG_STREAMING:
            loop_create_end = datetime.now()
            loop_create_delta = (loop_create_end - loop_create_start).total_seconds()
            print(f"[DEBUG_STREAMING] generate_speech_stream: Event loop created and set - took={loop_create_delta:.3f}s, timestamp={loop_create_end}")

        # Create the async generator for this text chunk
        async_generator = _generate_audio_stream(text_chunk, voice, speed)

        try:
            chunk_count = 0
            last_chunk_time = None

            while True:
                try:
                    if DEBUG_STREAMING:
                        retrieve_start = datetime.now()
                        if chunk_count == 0:
                            print(f"[DEBUG_STREAMING] generate_speech_stream: Retrieving first audio chunk from text chunk {text_chunk_idx + 1} - timestamp={retrieve_start}")
                        else:
                            if last_chunk_time:
                                retrieve_delta = (retrieve_start - last_chunk_time).total_seconds()
                                print(f"[DEBUG_STREAMING] generate_speech_stream: Retrieving audio chunk {chunk_count + 1} from text chunk {text_chunk_idx + 1} - timestamp={retrieve_start}, delta_from_last_yield={retrieve_delta:.3f}s")

                    next_chunk = loop.run_until_complete(async_generator.__anext__())

                    if DEBUG_STREAMING:
                        retrieve_end = datetime.now()
                        retrieve_delta = (retrieve_end - retrieve_start).total_seconds()
                        chunk_size = len(next_chunk)
                        print(f"[DEBUG_STREAMING] generate_speech_stream: Retrieved audio chunk - text_chunk={text_chunk_idx + 1}, audio_chunk={chunk_count + 1}, size={chunk_size} bytes, retrieval_took={retrieve_delta:.3f}s, timestamp={retrieve_end}")
                except StopAsyncIteration:
                    break

                chunk_count += 1
                total_audio_chunks += 1

                if DEBUG_STREAMING:
                    yield_time = datetime.now()
                    if last_chunk_time:
                        yield_delta = (yield_time - last_chunk_time).total_seconds()
                        print(f"[DEBUG_STREAMING] generate_speech_stream: Yielding audio chunk - text_chunk={text_chunk_idx + 1}, audio_chunk={chunk_count}, size={len(next_chunk)} bytes, timestamp={yield_time}, delta_from_last_yield={yield_delta:.3f}s")
                    else:
                        print(f"[DEBUG_STREAMING] generate_speech_stream: Yielding audio chunk - text_chunk={text_chunk_idx + 1}, audio_chunk={chunk_count}, size={len(next_chunk)} bytes, timestamp={yield_time}")

                yield next_chunk
                last_chunk_time = datetime.now() if DEBUG_STREAMING else None
        finally:
            # Best-effort cleanup of async generators and loop for this chunk
            if DEBUG_STREAMING:
                cleanup_start = datetime.now()
                print(f"[DEBUG_STREAMING] generate_speech_stream: Starting cleanup for text chunk {text_chunk_idx + 1} - timestamp={cleanup_start}")

            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

            if DEBUG_STREAMING:
                cleanup_end = datetime.now()
                cleanup_delta = (cleanup_end - cleanup_start).total_seconds()
                chunk_total_delta = (cleanup_end - chunk_start_time).total_seconds()
                print(f"[DEBUG_STREAMING] generate_speech_stream: Cleanup completed for text chunk {text_chunk_idx + 1} - cleanup_took={cleanup_delta:.3f}s, chunk_time={chunk_total_delta:.3f}s, audio_chunks={chunk_count}, timestamp={cleanup_end}")

    if DEBUG_STREAMING:
        end_time = datetime.now()
        total_delta = (end_time - start_time).total_seconds()
        print(f"[DEBUG_STREAMING] generate_speech_stream: All chunks completed - text_chunks={len(text_chunks)}, total_audio_chunks={total_audio_chunks}, total_time={total_delta:.3f}s, timestamp={end_time}")


async def _generate_audio(text, voice, response_format, speed):
    """Generate TTS audio and optionally convert to a different format."""
    # Determine if the voice is an OpenAI-compatible voice or a direct edge-tts voice
    edge_tts_voice = voice_mapping.get(voice, voice)  # Use mapping if in OpenAI names, otherwise use as-is
    text = prepare_edge_tts_input(edge_tts_voice, text)

    # Generate the TTS output in mp3 format first
    temp_mp3_file_obj = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    temp_mp3_path = temp_mp3_file_obj.name

    # Convert speed to SSML rate format
    try:
        speed_rate = speed_to_rate(speed)  # Convert speed value to "+X%" or "-X%"
    except Exception as e:
        print(f"Error converting speed: {e}. Defaulting to +0%.")
        speed_rate = "+0%"

    # Force a fresh aiohttp connector to prevent connection pooling issues
    connector = aiohttp.TCPConnector(force_close=True)

    # Generate the MP3 file
    communicator = edge_tts.Communicate(text=text, voice=edge_tts_voice, rate=speed_rate, connector=connector)
    await communicator.save(temp_mp3_path)

    temp_mp3_file_obj.close()  # Explicitly close our file object for the initial mp3

    # If the requested format is mp3, return the generated file directly
    if response_format == "mp3":
        return temp_mp3_path

    # Check if FFmpeg is installed
    if not is_ffmpeg_installed():
        print("FFmpeg is not available. Returning unmodified mp3 file.")
        return temp_mp3_path  # Return the original mp3 path, it won't be cleaned by this function

    # Create a new temporary file for the converted output
    converted_file_obj = tempfile.NamedTemporaryFile(delete=False, suffix=f".{response_format}")
    converted_path = converted_file_obj.name
    converted_file_obj.close()  # Close file object, ffmpeg will write to the path

    # Build the FFmpeg command
    ffmpeg_command = [
        "ffmpeg",
        "-i", temp_mp3_path,  # Input file path
        "-c:a", {
            "aac": "aac",
            "mp3": "libmp3lame",
            "wav": "pcm_s16le",
            "opus": "libopus",
            "flac": "flac"
        }.get(response_format, "aac"),  # Default to AAC if unknown
    ]

    if response_format != "wav":
        ffmpeg_command.extend(["-b:a", "192k"])

    ffmpeg_command.extend([
        "-f", {
            "aac": "mp4",  # AAC in MP4 container
            "mp3": "mp3",
            "wav": "wav",
            "opus": "ogg",
            "flac": "flac"
        }.get(response_format, response_format),  # Default to matching format
        "-y",  # Overwrite without prompt
        converted_path  # Output file path
    ])

    try:
        # Run FFmpeg command and ensure no errors occur
        subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        # Clean up potentially created (but incomplete) converted file
        Path(converted_path).unlink(missing_ok=True)
        # Clean up the original mp3 file as well, since conversion failed
        Path(temp_mp3_path).unlink(missing_ok=True)

        if DETAILED_ERROR_LOGGING:
            error_message = f"FFmpeg error during audio conversion. Command: '{' '.join(e.cmd)}'. Stderr: {e.stderr.decode('utf-8', 'ignore')}"
            print(error_message)  # Log for server-side diagnosis
        else:
            error_message = f"FFmpeg error during audio conversion: {e}"
            print(error_message)  # Log a simpler message
        raise RuntimeError(f"FFmpeg error during audio conversion: {e}")  # The raised error will still have details via e

    # Clean up the original temporary file (original mp3) as it's now converted
    Path(temp_mp3_path).unlink(missing_ok=True)

    return converted_path


def generate_speech(text, voice, response_format, speed=1.0):
    """Generate speech audio with intelligent text chunking for large texts."""
    # Validate input text
    if not text or not text.strip():
        raise ValueError("Text cannot be empty or whitespace-only")

    # Determine if we should chunk the text
    should_chunk = ENABLE_TEXT_CHUNKING and len(text) > TEXT_CHUNK_THRESHOLD

    if should_chunk:
        text_chunks = chunk_text_intelligently(text, TEXT_CHUNK_THRESHOLD)
        # Filter out empty/whitespace chunks
        text_chunks = [chunk for chunk in text_chunks if chunk and chunk.strip()]
        if DEBUG_STREAMING:
            print(f"[DEBUG_STREAMING] generate_speech: Text chunked into {len(text_chunks)} chunks - chunk_sizes={[len(c) for c in text_chunks]}")
    else:
        text_chunks = [text]
        if DEBUG_STREAMING and len(text) > 0:
            print(f"[DEBUG_STREAMING] generate_speech: Text not chunked (length={len(text)}, threshold={TEXT_CHUNK_THRESHOLD}, enabled={ENABLE_TEXT_CHUNKING})")

    # If all chunks were filtered out, raise an error
    if not text_chunks:
        raise ValueError("All text chunks were empty or whitespace-only after processing")

    # If only one chunk, use the original logic
    if len(text_chunks) == 1:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_generate_audio(text, voice, response_format, speed))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    # Multiple chunks - generate audio for each and concatenate
    chunk_files = []
    try:
        for idx, text_chunk in enumerate(text_chunks):
            # Skip empty or whitespace-only chunks
            if not text_chunk or not text_chunk.strip():
                if DEBUG_STREAMING:
                    print(f"[DEBUG_STREAMING] generate_speech: Skipping empty/whitespace chunk {idx + 1}/{len(text_chunks)}")
                continue

            if DEBUG_STREAMING:
                print(f"[DEBUG_STREAMING] generate_speech: Generating audio for chunk {idx + 1}/{len(text_chunks)} - length={len(text_chunk)}")

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                chunk_file = loop.run_until_complete(_generate_audio(text_chunk, voice, response_format, speed))
                chunk_files.append(chunk_file)
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                asyncio.set_event_loop(None)
                loop.close()

        # Concatenate all audio files using FFmpeg
        if DEBUG_STREAMING:
            print(f"[DEBUG_STREAMING] generate_speech: Concatenating {len(chunk_files)} audio files")

        # Check if FFmpeg is installed
        if not is_ffmpeg_installed():
            print("FFmpeg is not available. Returning first chunk only.")
            # Return the first chunk and clean up the rest
            first_chunk = chunk_files[0]
            for chunk_file in chunk_files[1:]:
                Path(chunk_file).unlink(missing_ok=True)
            return first_chunk

        # Create a temporary file for the concatenated output
        output_file_obj = tempfile.NamedTemporaryFile(delete=False, suffix=f".{response_format}")
        output_path = output_file_obj.name
        output_file_obj.close()

        # Create a file list for FFmpeg concat demuxer
        concat_list_obj = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
        concat_list_path = concat_list_obj.name
        for chunk_file in chunk_files:
            # FFmpeg concat demuxer requires absolute paths and proper escaping
            concat_list_obj.write(f"file '{os.path.abspath(chunk_file)}'\n")
        concat_list_obj.close()

        try:
            # Use FFmpeg concat demuxer to concatenate audio files
            ffmpeg_command = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",  # Copy codec without re-encoding for speed
                "-y",  # Overwrite without prompt
                output_path
            ]

            subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if DEBUG_STREAMING:
                print(f"[DEBUG_STREAMING] generate_speech: Concatenation complete - output_size={os.path.getsize(output_path)} bytes")

            return output_path
        except subprocess.CalledProcessError as e:
            # Clean up output file if concatenation failed
            Path(output_path).unlink(missing_ok=True)

            if DETAILED_ERROR_LOGGING:
                error_message = f"FFmpeg error during audio concatenation. Command: '{' '.join(e.cmd)}'. Stderr: {e.stderr.decode('utf-8', 'ignore')}"
                print(error_message)
            else:
                error_message = f"FFmpeg error during audio concatenation: {e}"
                print(error_message)

            # Fall back to returning the first chunk
            print("Falling back to first chunk only due to concatenation error")
            first_chunk = chunk_files[0]
            for chunk_file in chunk_files[1:]:
                Path(chunk_file).unlink(missing_ok=True)
            return first_chunk
        finally:
            # Clean up concat list file
            Path(concat_list_path).unlink(missing_ok=True)
    finally:
        # Clean up all chunk files (they've been concatenated or we're done with them)
        for chunk_file in chunk_files:
            Path(chunk_file).unlink(missing_ok=True)


def get_models():
    return model_data


def get_models_formatted():
    return [{"id": x["id"]} for x in model_data]


def get_voices_formatted():
    return [{"id": k, "name": v} for k, v in voice_mapping.items()]


async def _get_voices(language=None):
    # List all voices, filter by language if specified
    all_voices = await edge_tts.list_voices()
    language = language or DEFAULT_LANGUAGE  # Use default if no language specified

    # Filter by language and exclude multilingual voices
    # Multilingual voices can auto-detect language which causes inconsistent behavior
    filtered_voices = [
        {"name": v['ShortName'], "gender": v['Gender'], "language": v['Locale']}
        for v in all_voices
        if (language == 'all' or language is None or v['Locale'] == language)
        and 'Multilingual' not in v['ShortName']  # Exclude multilingual voices
    ]
    return filtered_voices


def get_voices(language=None):
    # Create a fresh event loop for each request to prevent cross-request state contamination
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_get_voices(language))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


def speed_to_rate(speed: float) -> str:
    """
    Converts a multiplicative speed value to the edge-tts "rate" format.

    Args:
        speed (float): The multiplicative speed value (e.g., 1.5 for +50%, 0.5 for -50%).

    Returns:
        str: The formatted "rate" string (e.g., "+50%" or "-50%").
    """
    if speed < 0 or speed > 2:
        raise ValueError("Speed must be between 0 and 2 (inclusive).")

    # Convert speed to percentage change
    percentage_change = (speed - 1) * 100

    # Format with a leading "+" or "-" as required
    return f"{percentage_change:+.0f}%"
