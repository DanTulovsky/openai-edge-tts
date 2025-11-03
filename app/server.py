# server.py

from flask import Flask, request, send_file, jsonify, Response, make_response, render_template
from gevent.pywsgi import WSGIServer
from dotenv import load_dotenv
import os
import traceback
import json
import base64
from datetime import datetime, timedelta

from config import DEFAULT_CONFIGS, VERSION
from handle_text import prepare_tts_input_with_context
from tts_handler import generate_speech, generate_speech_stream, get_models_formatted, get_voices, get_voices_formatted, is_ffmpeg_installed
from utils import getenv_bool, require_api_key, AUDIO_FORMAT_MIME_TYPES, DETAILED_ERROR_LOGGING, DEBUG_STREAMING
# HLS support removed - previously imported hls_handler here
import threading
import uuid

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


# In-memory active streams registry for short-lived progressive streams
_active_streams = {}


@app.route('/v1/audio/speech/init', methods=['POST', 'OPTIONS'])
@require_api_key
def init_speech():
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response

    data = request.json or {}
    if 'input' not in data:
        return jsonify({'error': "Missing 'input' in request body"}), 400

    stream_id = str(uuid.uuid4())
    token = uuid.uuid4().hex
    expires_at = datetime.now() + timedelta(seconds=60)  # 60s token lifetime
    _active_streams[stream_id] = {
        'input': data.get('input'),
        'voice': data.get('voice', DEFAULT_VOICE),
        'speed': float(data.get('speed', DEFAULT_SPEED)),
        'response_format': data.get('response_format', DEFAULT_RESPONSE_FORMAT),
        'token': token,
        'expires_at': expires_at
    }

    return jsonify({'stream_id': stream_id, 'token': token})


@app.route('/v1/audio/speech/stream/<stream_id>', methods=['GET'])
def stream_speech(stream_id):
    params = _active_streams.get(stream_id)
    if not params:
        return jsonify({'error': 'Stream not found'}), 404

    # Validate token query param (we don't require Authorization header for audio element requests)
    token = request.args.get('token')
    if not token or token != params.get('token'):
        return jsonify({'error': 'Unauthorized'}), 401

    # Check expiry
    expires_at = params.get('expires_at')
    if expires_at and datetime.now() > expires_at:
        _active_streams.pop(stream_id, None)
        return jsonify({'error': 'Stream expired'}), 410

    def generate_and_cleanup():
        try:
            for chunk in generate_raw_audio_stream(params['input'], params['voice'], params['speed']):
                yield chunk
        finally:
            try:
                _active_streams.pop(stream_id, None)
            except Exception:
                pass

    return Response(
        generate_and_cleanup(),
        mimetype='audio/mpeg',
        headers={
            'Cache-Control': 'no-cache',
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*'
        }
    )

# OpenAI endpoint format


@app.route('/v1/audio/speech', methods=['POST', 'OPTIONS'])
@app.route('/audio/speech', methods=['POST', 'OPTIONS'])  # Add this line for the alias
@require_api_key
def text_to_speech():
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response

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
            # HLS removed: instruct clients to use progressive streaming
            return jsonify({"error": "HLS streaming is no longer supported. Use 'audio_stream' or 'audio' formats."}), 400

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
                    'X-Accel-Buffering': 'no',  # Disable nginx buffering
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
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
                    'X-Accel-Buffering': 'no',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
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
                    'Content-Length': str(len(audio_data)),
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
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


@app.route('/test', methods=['GET'])
def test_page():
    """Serve a test HTML page for TTS testing with OpenAI SDK playAudio helper."""
    return render_template('test.html')


print(f" Edge TTS (Free Azure TTS) Replacement for OpenAI's TTS API")
print(f" Version: {VERSION}")
print(f" ")
print(f" * Serving OpenAI Edge TTS")
print(f" * Server running on http://localhost:{PORT}")
print(f" * TTS Endpoint: http://localhost:{PORT}/v1/audio/speech")
print(f" * DEBUG_STREAMING: {'ENABLED - Streaming debug logs will be output' if DEBUG_STREAMING else 'DISABLED'}")
print(f" * HLS Support: REMOVED")
print(f" ")

# Start HLS cleanup thread on server startup
# HLS cleanup thread removed

if __name__ == '__main__':
    # Check if debug mode is enabled via environment variable
    flask_debug = getenv_bool('FLASK_DEBUG', False) or os.getenv('FLASK_ENV') == 'development'

    if flask_debug:
        # Use Flask's development server for better debugging (auto-reload, better error pages)
        print(f" * Flask Debug Mode: ENABLED")
        print(f" * Auto-reload: ENABLED")
        app.run(host='0.0.0.0', port=PORT, debug=True, threaded=False)
    else:
        # Use gevent WSGI server for production
        # Silence gevent's per-request access logs to keep test output compact
        http_server = WSGIServer(('0.0.0.0', PORT), app, log=None)
        http_server.serve_forever()
