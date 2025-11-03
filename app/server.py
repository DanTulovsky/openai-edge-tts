# server.py

from flask import Flask, request, send_file, jsonify, Response, make_response
from gevent.pywsgi import WSGIServer
from dotenv import load_dotenv
import os
import traceback
import json
import base64
from datetime import datetime

from config import DEFAULT_CONFIGS, VERSION
from handle_text import prepare_tts_input_with_context
from tts_handler import generate_speech, generate_speech_stream, get_models_formatted, get_voices, get_voices_formatted, is_ffmpeg_installed
from utils import getenv_bool, require_api_key, AUDIO_FORMAT_MIME_TYPES, DETAILED_ERROR_LOGGING, DEBUG_STREAMING
from hls_handler import (
    create_hls_session, get_hls_session, generate_hls_stream,
    start_cleanup_thread, HLS_SEGMENT_DURATION
)
import threading

app = Flask(__name__)
load_dotenv()

API_KEY = os.getenv('API_KEY', DEFAULT_CONFIGS["API_KEY"])
PORT = int(os.getenv('PORT', str(DEFAULT_CONFIGS["PORT"])))

DEFAULT_VOICE = os.getenv('DEFAULT_VOICE', DEFAULT_CONFIGS["DEFAULT_VOICE"])
DEFAULT_RESPONSE_FORMAT = os.getenv('DEFAULT_RESPONSE_FORMAT', DEFAULT_CONFIGS["DEFAULT_RESPONSE_FORMAT"])
DEFAULT_SPEED = float(os.getenv('DEFAULT_SPEED', str(DEFAULT_CONFIGS["DEFAULT_SPEED"])))

REMOVE_FILTER = getenv_bool('REMOVE_FILTER', DEFAULT_CONFIGS["REMOVE_FILTER"])
EXPAND_API = getenv_bool('EXPAND_API', DEFAULT_CONFIGS["EXPAND_API"])

# DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'tts-1')

# Currently in "beta" — needs more extensive testing where drop-in replacement warranted


def generate_sse_audio_stream(text, voice, speed):
    """Generator function for SSE streaming with JSON events."""
    try:
        # Generate streaming audio chunks and convert to SSE format
        for chunk in generate_speech_stream(text, voice, speed):
            # Base64 encode the audio chunk
            encoded_audio = base64.b64encode(chunk).decode('utf-8')

            # Create SSE event for audio delta
            event_data = {
                "type": "speech.audio.delta",
                "audio": encoded_audio
            }

            # Format as SSE event
            yield f"data: {json.dumps(event_data)}\n\n"

        # Send completion event
        completion_event = {
            "type": "speech.audio.done",
            "usage": {
                "input_tokens": len(text.split()),  # Rough estimate
                "output_tokens": 0,  # Edge TTS doesn't provide this
                "total_tokens": len(text.split())
            }
        }
        yield f"data: {json.dumps(completion_event)}\n\n"

    except Exception as e:
        print(f"Error during SSE streaming: {e}")
        # Send error event
        error_event = {
            "type": "error",
            "error": str(e)
        }
        yield f"data: {json.dumps(error_event)}\n\n"

# Raw audio streaming for low-latency playback


def generate_raw_audio_stream(text, voice, speed):
    """Generator function for raw audio streaming (following SSE pattern)."""
    if DEBUG_STREAMING:
        start_time = datetime.now()
        print(f"[DEBUG_STREAMING] generate_raw_audio_stream: Entry - text_length={len(text)}, voice={voice}, speed={speed}, timestamp={start_time}")

    try:
        chunk_count = 0
        last_chunk_time = None

        # Use existing streaming infrastructure
        for chunk in generate_speech_stream(text, voice, speed):
            chunk_received_time = datetime.now()
            chunk_size = len(chunk)

            if DEBUG_STREAMING:
                if chunk_count == 0:
                    first_chunk_delta = (chunk_received_time - start_time).total_seconds()
                    print(f"[DEBUG_STREAMING] generate_raw_audio_stream: First chunk received - size={chunk_size} bytes, timestamp={chunk_received_time}, delta_from_start={first_chunk_delta:.3f}s")
                else:
                    if last_chunk_time:
                        chunk_delta = (chunk_received_time - last_chunk_time).total_seconds()
                        print(f"[DEBUG_STREAMING] generate_raw_audio_stream: Chunk received - chunk_num={chunk_count + 1}, size={chunk_size} bytes, timestamp={chunk_received_time}, delta_from_last_chunk={chunk_delta:.3f}s")
                    else:
                        print(f"[DEBUG_STREAMING] generate_raw_audio_stream: Chunk received - chunk_num={chunk_count + 1}, size={chunk_size} bytes, timestamp={chunk_received_time}")

            chunk_count += 1

            if DEBUG_STREAMING:
                yield_time = datetime.now()
                yield_delta = (yield_time - chunk_received_time).total_seconds()
                print(f"[DEBUG_STREAMING] generate_raw_audio_stream: Yielding chunk to Flask Response - chunk_num={chunk_count}, size={chunk_size} bytes, timestamp={yield_time}, delta_from_receive={yield_delta:.3f}s")

            yield chunk  # Yield raw audio bytes directly
            last_chunk_time = datetime.now() if DEBUG_STREAMING else None

        if DEBUG_STREAMING:
            end_time = datetime.now()
            total_delta = (end_time - start_time).total_seconds()
            print(f"[DEBUG_STREAMING] generate_raw_audio_stream: Completed - total_chunks={chunk_count}, total_time={total_delta:.3f}s, timestamp={end_time}")
    except Exception as e:
        if DEBUG_STREAMING:
            error_time = datetime.now()
            print(f"[DEBUG_STREAMING] generate_raw_audio_stream: Error - {e}, timestamp={error_time}")
        print(f"Error during raw audio streaming: {e}")
        return

# OpenAI endpoint format


@app.route('/v1/audio/speech', methods=['POST'])
@app.route('/audio/speech', methods=['POST'])  # Add this line for the alias
@require_api_key
def text_to_speech():
    request_start_time = datetime.now() if DEBUG_STREAMING else None

    try:
        data = request.json
        if not data or 'input' not in data:
            return jsonify({"error": "Missing 'input' in request body"}), 400

        text = data.get('input')

        if not REMOVE_FILTER:
            text = prepare_tts_input_with_context(text)

        # model = data.get('model', DEFAULT_MODEL)
        voice = data.get('voice', DEFAULT_VOICE)
        response_format = data.get('response_format', DEFAULT_RESPONSE_FORMAT)
        speed = float(data.get('speed', DEFAULT_SPEED))

        # Check stream format - "sse" or "audio_stream" trigger streaming
        stream_format = data.get('stream_format', 'audio_stream')  # 'audio_stream' (default), 'audio', 'sse', 'hls'

        if DEBUG_STREAMING:
            request_params_time = datetime.now()
            print(
                f"[DEBUG_STREAMING] text_to_speech: Request received - text_length={len(text)}, voice={voice}, response_format={response_format}, speed={speed}, stream_format={stream_format}, model={data.get('model', 'N/A')}, timestamp={request_params_time}")

        mime_type = AUDIO_FORMAT_MIME_TYPES.get(response_format, "audio/mpeg")

        if stream_format == 'hls':
            # HLS streaming for iOS Safari support
            if not is_ffmpeg_installed():
                return jsonify({"error": "HLS streaming requires FFmpeg to be installed. Please install FFmpeg or use a different stream_format."}), 400

            # Allow mp3 or aac for HLS
            if response_format not in ('mp3', 'aac'):
                return jsonify({"error": "HLS streaming supports 'mp3' or 'aac' response_format"}), 400

            segment_duration = float(data.get('hls_segment_duration', HLS_SEGMENT_DURATION))

            # Create HLS session with codec
            session_id = create_hls_session(segment_duration, codec=response_format)

            thread = threading.Thread(
                target=generate_hls_stream,
                args=(text, voice, speed, session_id),
                daemon=True
            )
            thread.start()

            playlist_url = f"/v1/audio/speech/hls/{session_id}/playlist.m3u8"
            return jsonify({"playlist_url": playlist_url})

        if stream_format == 'sse':
            # Return SSE streaming response with JSON events
            def generate_sse():
                for event in generate_sse_audio_stream(text, voice, speed):
                    yield event

            return Response(
                generate_sse(),
                mimetype='text/event-stream',
                headers={
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'  # Disable nginx buffering
                }
            )
        elif stream_format == 'audio_stream':
            # Return raw audio streaming (follows SSE pattern)
            if DEBUG_STREAMING:
                stream_request_time = datetime.now()
                print(f"[DEBUG_STREAMING] text_to_speech: audio_stream format detected - text_length={len(text)}, voice={voice}, speed={speed}, timestamp={stream_request_time}")

            first_byte_sent = False
            first_byte_time = None
            stream_complete_time = None
            total_bytes_sent = 0

            def generate():
                nonlocal first_byte_sent, first_byte_time, stream_complete_time, total_bytes_sent

                if DEBUG_STREAMING:
                    generate_start = datetime.now()
                    generate_delta = (generate_start - stream_request_time).total_seconds()
                    print(f"[DEBUG_STREAMING] text_to_speech: Streaming begins (generate function called) - timestamp={generate_start}, delta_from_request={generate_delta:.3f}s")

                try:
                    for chunk in generate_raw_audio_stream(text, voice, speed):
                        if not first_byte_sent:
                            first_byte_sent = True
                            first_byte_time = datetime.now()
                            first_byte_delta = (first_byte_time - request_start_time).total_seconds() if request_start_time else 0
                            chunk_size = len(chunk)
                            total_bytes_sent = chunk_size
                            if DEBUG_STREAMING:
                                print(f"[DEBUG_STREAMING] text_to_speech: FIRST AUDIO BYTE SENT TO CLIENT - size={chunk_size} bytes, timestamp={first_byte_time}, time_from_request_start={first_byte_delta:.3f}s")
                        else:
                            total_bytes_sent += len(chunk)

                        yield chunk

                    stream_complete_time = datetime.now()
                    if DEBUG_STREAMING:
                        stream_duration = (stream_complete_time - first_byte_time).total_seconds() if first_byte_time else 0
                        total_time = (stream_complete_time - request_start_time).total_seconds() if request_start_time else 0
                        print(f"[DEBUG_STREAMING] text_to_speech: STREAMING COMPLETE - total_bytes={total_bytes_sent}, stream_duration={stream_duration:.3f}s, total_time_from_request={total_time:.3f}s, timestamp={stream_complete_time}")
                except Exception as e:
                    if DEBUG_STREAMING:
                        error_time = datetime.now()
                        print(f"[DEBUG_STREAMING] text_to_speech: STREAMING ERROR - {e}, timestamp={error_time}")
                    raise

            return Response(
                generate(),
                mimetype=mime_type,
                headers={
                    'Content-Type': mime_type,
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )
        else:
            # Return raw audio data (like OpenAI) - can be piped to ffplay
            output_file_path = generate_speech(text, voice, response_format, speed)

            # Read the file and return raw audio data
            with open(output_file_path, 'rb') as audio_file:
                audio_data = audio_file.read()

            # Clean up the temporary file
            try:
                os.unlink(output_file_path)
            except OSError:
                pass  # File might already be cleaned up

            return Response(
                audio_data,
                mimetype=mime_type,
                headers={
                    'Content-Type': mime_type,
                    'Content-Length': str(len(audio_data))
                }
            )

    except Exception as e:
        if DETAILED_ERROR_LOGGING:
            app.logger.error(f"Error in text_to_speech: {str(e)}\n{traceback.format_exc()}")
        else:
            app.logger.error(f"Error in text_to_speech: {str(e)}")
        # Return a 500 error for unhandled exceptions, which is more standard than 400
        return jsonify({"error": "An internal server error occurred", "details": str(e)}), 500

# OpenAI endpoint format


@app.route('/v1/models', methods=['GET', 'POST'])
@app.route('/models', methods=['GET', 'POST'])
@app.route('/v1/audio/models', methods=['GET', 'POST'])
@app.route('/audio/models', methods=['GET', 'POST'])
def list_models():
    return jsonify({"models": get_models_formatted()})

# OpenAI endpoint format


@app.route('/v1/audio/voices', methods=['GET', 'POST'])
@app.route('/audio/voices', methods=['GET', 'POST'])
def list_voices_formatted():
    return jsonify({"voices": get_voices_formatted()})


@app.route('/v1/voices', methods=['GET', 'POST'])
@app.route('/voices', methods=['GET', 'POST'])
@require_api_key
def list_voices():
    specific_language = None

    data = request.args if request.method == 'GET' else request.json
    if data and ('language' in data or 'locale' in data):
        specific_language = data.get('language') if 'language' in data else data.get('locale')

    return jsonify({"voices": get_voices(specific_language)})


@app.route('/v1/voices/all', methods=['GET', 'POST'])
@app.route('/voices/all', methods=['GET', 'POST'])
@require_api_key
def list_all_voices():
    return jsonify({"voices": get_voices('all')})


"""
Support for ElevenLabs and Azure AI Speech
    (currently in beta)
"""

# http://localhost:5050/elevenlabs/v1/text-to-speech
# http://localhost:5050/elevenlabs/v1/text-to-speech/en-US-AndrewNeural


@app.route('/elevenlabs/v1/text-to-speech/<voice_id>', methods=['POST'])
@require_api_key
def elevenlabs_tts(voice_id):
    if not EXPAND_API:
        return jsonify({"error": f"Endpoint not allowed"}), 500

    # Parse the incoming JSON payload
    try:
        payload = request.json
        if not payload or 'text' not in payload:
            return jsonify({"error": "Missing 'text' in request body"}), 400
    except Exception as e:
        return jsonify({"error": f"Invalid JSON payload: {str(e)}"}), 400

    text = payload['text']

    if not REMOVE_FILTER:
        text = prepare_tts_input_with_context(text)

    voice = voice_id  # ElevenLabs uses the voice_id in the URL

    # Use default settings for edge-tts
    response_format = 'mp3'
    speed = DEFAULT_SPEED  # Optional customization via payload.get('speed', DEFAULT_SPEED)

    # Generate speech using edge-tts
    try:
        output_file_path = generate_speech(text, voice, response_format, speed)
    except Exception as e:
        return jsonify({"error": f"TTS generation failed: {str(e)}"}), 500

    # Return the generated audio file
    return send_file(output_file_path, mimetype="audio/mpeg", as_attachment=True, download_name="speech.mp3")

# tts.speech.microsoft.com/cognitiveservices/v1
# https://{region}.tts.speech.microsoft.com/cognitiveservices/v1
# http://localhost:5050/azure/cognitiveservices/v1


@app.route('/azure/cognitiveservices/v1', methods=['POST'])
@require_api_key
def azure_tts():
    if not EXPAND_API:
        return jsonify({"error": f"Endpoint not allowed"}), 500

    # Parse the SSML payload
    try:
        ssml_data = request.data.decode('utf-8')
        if not ssml_data:
            return jsonify({"error": "Missing SSML payload"}), 400

        # Extract the text and voice from SSML
        from xml.etree import ElementTree as ET
        root = ET.fromstring(ssml_data)
        text = root.find('.//{http://www.w3.org/2001/10/synthesis}voice').text
        voice = root.find('.//{http://www.w3.org/2001/10/synthesis}voice').get('name')
    except Exception as e:
        return jsonify({"error": f"Invalid SSML payload: {str(e)}"}), 400

    # Use default settings for edge-tts
    response_format = 'mp3'
    speed = DEFAULT_SPEED

    if not REMOVE_FILTER:
        text = prepare_tts_input_with_context(text)

    # Generate speech using edge-tts
    try:
        output_file_path = generate_speech(text, voice, response_format, speed)
    except Exception as e:
        return jsonify({"error": f"TTS generation failed: {str(e)}"}), 500

    # Return the generated audio file
    return send_file(output_file_path, mimetype="audio/mpeg", as_attachment=True, download_name="speech.mp3")


# HLS endpoint handlers
@app.route('/v1/audio/speech/hls/<session_id>/playlist.m3u8', methods=['GET'])
@app.route('/audio/speech/hls/<session_id>/playlist.m3u8', methods=['GET'])
def serve_hls_playlist(session_id):
    """Serve the HLS playlist file."""
    session = get_hls_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    # Wait for playlist file to be created (aac/ffmpeg path may take a moment)
    if not session.playlist_path.exists():
        import time as _t
        waited = 0.0
        while waited < 5.0 and not session.playlist_path.exists():
            _t.sleep(0.05)
            waited += 0.05
        # After waiting, if still not present, report generating
        if not session.playlist_path.exists():
            return jsonify({"error": "Playlist not yet available"}), 404

    # Check if playlist has segments and if there's an error
    with session.lock:
        segment_counter = getattr(session, 'segment_counter', 0)
        is_completed = getattr(session, 'completed', False)
        has_error = getattr(session, 'error', None) is not None
        error_message = getattr(session, 'error', None)

    # Check for actual segment files on disk (works for both mp3 and aac)
    segment_files = list(session.segment_dir.glob("segment*.*"))
    has_segment_files = len(segment_files) > 0

    # Read and validate playlist content
    playlist_content = ""
    playlist_has_segments = False
    playlist_lines = []

    try:
        with open(session.playlist_path, 'r') as f:
            playlist_content = f.read()
            playlist_lines = playlist_content.split('\n')
            # Check for segment references lines (not starting with #)
            playlist_has_segments = any(
                ('.mp3' in line.lower() or '.m4s' in line.lower() or '.m4a' in line.lower() or '.ts' in line.lower() or 'segment' in line.lower())
                for line in playlist_lines if line and not line.strip().startswith('#')
            )
    except Exception as e:
        if DETAILED_ERROR_LOGGING:
            print(f"Error reading playlist file: {e}")

    # If there's an error, return it clearly
    if has_error:
        return jsonify({
            "error": "HLS generation failed",
            "details": error_message
        }), 500

    # Validate that we have segments (check both counter and actual files)
    has_segments = segment_counter > 0 and has_segment_files and playlist_has_segments

    # If completed but no segments, that's a critical error
    if is_completed and not has_segments:
        if DETAILED_ERROR_LOGGING:
            print(f"HLS playlist validation failed - session_id={session_id}")
            print(f"  segment_counter={segment_counter}")
            print(f"  segment_files={len(segment_files)}")
            print(f"  playlist_has_segments={playlist_has_segments}")
            print(f"  playlist_lines={len(playlist_lines)}")
            print(f"  playlist_content preview: {playlist_content[:200]}")

        return jsonify({
            "error": "HLS playlist is empty (no segments)",
            "details": "The server may still be generating segments, or HLS implementation is incomplete. No segment files were found.",
            "debug": {
                "segment_counter": segment_counter,
                "segment_files_count": len(segment_files),
                "playlist_has_segment_refs": playlist_has_segments,
                "playlist_line_count": len(playlist_lines)
            }
        }), 500

    # If no segments yet and not completed, wait until the first segment exists.
    # This guarantees the FIRST client request gets a valid HLS playlist (.m3u8).
    if not has_segments and not is_completed:
        if DETAILED_ERROR_LOGGING:
            print(f"HLS playlist not ready yet - session_id={session_id}, segment_counter={segment_counter}. Waiting for first segment…")

        import time as _t
        start_wait = _t.time()
        max_wait = 20.0
        while True:
            with session.lock:
                current_counter = getattr(session, 'segment_counter', 0)
                error_now = getattr(session, 'error', None) is not None
                completed_now = getattr(session, 'completed', False)
            if error_now or completed_now:
                break

            if current_counter > 0:
                # Verify the playlist file actually contains at least one segment reference
                try:
                    with open(session.playlist_path, 'r') as f:
                        content_now = f.read()
                        if '#EXTINF' in content_now:
                            has_segments = True
                            break
                except Exception:
                    pass
            _t.sleep(0.05)  # 50ms
            if (_t.time() - start_wait) > max_wait:
                # Timed out waiting; advise client to retry
                return make_response(jsonify({
                    "error": "Playlist not yet available",
                    "message": "Timed out waiting for first HLS segment.",
                    "status": "generating"
                }), 503)

        # Re-validate state after waiting
        with session.lock:
            is_completed = session.completed
            has_error = session.error is not None
            error_message = session.error
            segment_counter = session.segment_counter
        if has_error:
            return jsonify({"error": "HLS generation failed", "details": error_message}), 500
        if segment_counter == 0:
            # Generation completed with no segments
            return jsonify({"error": "HLS playlist is empty (no segments)"}), 500

    # Serve the actual playlist
    response = make_response(send_file(
        session.playlist_path,
        mimetype='application/vnd.apple.mpegurl'
    ))

    response.headers['Content-Type'] = 'application/vnd.apple.mpegurl'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


@app.route('/v1/audio/speech/hls/<session_id>/<segment_filename>', methods=['GET'])
@app.route('/audio/speech/hls/<session_id>/<segment_filename>', methods=['GET'])
def serve_hls_segment(session_id, segment_filename):
    """Serve an HLS segment file."""
    session = get_hls_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    segment_path = session.get_segment_path(segment_filename)
    if not segment_path:
        return jsonify({"error": "Segment not found"}), 404

    # Set MIME based on extension
    ext = os.path.splitext(segment_filename)[1].lower()
    if ext in ('.m4s', '.m4a', '.mp4'):
        seg_mime = 'audio/mp4'
    elif ext == '.ts':
        seg_mime = 'video/mp2t'
    elif ext == '.mp3':
        seg_mime = 'audio/mpeg'
    else:
        seg_mime = 'application/octet-stream'

    response = make_response(send_file(
        segment_path,
        mimetype=seg_mime
    ))
    response.headers['Content-Type'] = seg_mime
    response.headers['Cache-Control'] = 'public, max-age=3600'
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


print(f" Edge TTS (Free Azure TTS) Replacement for OpenAI's TTS API")
print(f" Version: {VERSION}")
print(f" ")
print(f" * Serving OpenAI Edge TTS")
print(f" * Server running on http://localhost:{PORT}")
print(f" * TTS Endpoint: http://localhost:{PORT}/v1/audio/speech")
print(f" * DEBUG_STREAMING: {'ENABLED - Streaming debug logs will be output' if DEBUG_STREAMING else 'DISABLED'}")
print(f" * HLS Support: {'ENABLED' if is_ffmpeg_installed() else 'DISABLED (FFmpeg not installed)'}")
print(f" ")

# Start HLS cleanup thread on server startup
if is_ffmpeg_installed():
    start_cleanup_thread()

if __name__ == '__main__':
    # Silence gevent's per-request access logs to keep test output compact
    http_server = WSGIServer(('0.0.0.0', PORT), app, log=None)
    http_server.serve_forever()
