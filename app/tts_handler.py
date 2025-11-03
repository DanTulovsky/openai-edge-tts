# tts_handler.py

import edge_tts
import asyncio
import tempfile
import subprocess
import os
from pathlib import Path
from datetime import datetime

from utils import DETAILED_ERROR_LOGGING, DEBUG_STREAMING
from config import DEFAULT_CONFIGS

# Language default (environment variable)
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', DEFAULT_CONFIGS["DEFAULT_LANGUAGE"])

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


async def _generate_audio_stream(text, voice, speed):
    """Generate streaming TTS audio using edge-tts."""
    if DEBUG_STREAMING:
        start_time = datetime.now()
        print(f"[DEBUG_STREAMING] _generate_audio_stream: Entry - text_length={len(text)}, voice={voice}, speed={speed}, timestamp={start_time}")

    # Determine if the voice is an OpenAI-compatible voice or a direct edge-tts voice
    edge_tts_voice = voice_mapping.get(voice, voice)  # Use mapping if in OpenAI names, otherwise use as-is

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

    communicator = edge_tts.Communicate(text=text, voice=edge_tts_voice, rate=speed_rate)

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
    """Generate streaming speech audio (synchronous wrapper)."""
    if DEBUG_STREAMING:
        start_time = datetime.now()
        print(f"[DEBUG_STREAMING] generate_speech_stream: Entry - text_length={len(text)}, timestamp={start_time}")

    # Drive the async generator from a dedicated event loop and yield chunks synchronously
    async_generator = _generate_audio_stream(text, voice, speed)

    if DEBUG_STREAMING:
        loop_create_start = datetime.now()
        print(f"[DEBUG_STREAMING] generate_speech_stream: Creating event loop - timestamp={loop_create_start}")

    loop = asyncio.new_event_loop()

    if DEBUG_STREAMING:
        loop_create_end = datetime.now()
        loop_create_delta = (loop_create_end - loop_create_start).total_seconds()
        print(f"[DEBUG_STREAMING] generate_speech_stream: Event loop created - took={loop_create_delta:.3f}s, timestamp={loop_create_end}")

    try:
        asyncio.set_event_loop(loop)
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


async def _generate_audio(text, voice, response_format, speed):
    """Generate TTS audio and optionally convert to a different format."""
    # Determine if the voice is an OpenAI-compatible voice or a direct edge-tts voice
    edge_tts_voice = voice_mapping.get(voice, voice)  # Use mapping if in OpenAI names, otherwise use as-is

    # Generate the TTS output in mp3 format first
    temp_mp3_file_obj = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    temp_mp3_path = temp_mp3_file_obj.name

    # Convert speed to SSML rate format
    try:
        speed_rate = speed_to_rate(speed)  # Convert speed value to "+X%" or "-X%"
    except Exception as e:
        print(f"Error converting speed: {e}. Defaulting to +0%.")
        speed_rate = "+0%"

    # Generate the MP3 file
    communicator = edge_tts.Communicate(text=text, voice=edge_tts_voice, rate=speed_rate)
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
    return asyncio.run(_generate_audio(text, voice, response_format, speed))


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
    filtered_voices = [
        {"name": v['ShortName'], "gender": v['Gender'], "language": v['Locale']}
        for v in all_voices if language == 'all' or language is None or v['Locale'] == language
    ]
    return filtered_voices


def get_voices(language=None):
    return asyncio.run(_get_voices(language))


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
