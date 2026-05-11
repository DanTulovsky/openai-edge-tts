"""
Italian TTS consistency: same voice + short text must yield identical MP3 bytes across repeated calls.

WET-163 was about ``it-IT-GiuseppeMultilingualNeural`` auto-detecting language (EN vs IT).
Microsoft Edge can still return two encodings/backends for that multilingual model, so CI used
flakey strict MD5 checks. These tests use a non-multilingual Italian neural voice instead
(recommended in ``get_voices``, which excludes Multilingual voices for the same reason).
"""

import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# Locale-locked Italian neural (deterministic Edge output in practice). GiuseppeMultilingual
# still alternates at the wire level even with SSML locale hints.
ITALIAN_VOICE = "it-IT-DiegoNeural"


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_server(port, timeout=10.0):
    url = f"http://127.0.0.1:{port}/test"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def tts_server():
    """Start the TTS server for testing."""
    port = get_free_port()

    project_root = Path(__file__).parent.parent
    server_script = project_root / "app" / "server.py"

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["API_KEY"] = "test_api_key"
    env["REQUIRE_API_KEY"] = "false"
    env["FLASK_DEBUG"] = "false"
    env["OPENAI_EDGE_TTS_TEST_MODE"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-u", str(server_script)],
        env=env,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if not wait_for_server(port, timeout=15.0):
        proc.kill()
        out, err = proc.communicate(timeout=5.0)
        pytest.fail(f"Server did not start in time. stdout: {out}, stderr: {err}")

    yield port

    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


def test_italian_voice_consistency_ciao(tts_server):
    """Test that Italian voice 'ciao' produces consistent output across multiple requests."""
    port = tts_server
    url = f"http://127.0.0.1:{port}/v1/audio/speech"

    payload = {
        "model": "tts-1",
        "voice": ITALIAN_VOICE,
        "input": "ciao",
        "response_format": "mp3",
        "speed": 1,
    }

    hashes = []
    num_requests = 5

    for _ in range(num_requests):
        response = requests.post(url, json=payload, timeout=10)
        assert response.status_code == 200

        content_hash = hashlib.md5(response.content).hexdigest()
        hashes.append(content_hash)

    unique_hashes = set(hashes)
    assert len(unique_hashes) == 1, (
        f"Italian voice is alternating! Found {len(unique_hashes)} different outputs. "
        f"Hashes: {unique_hashes}"
    )


def test_italian_voice_consistency_doppie(tts_server):
    """Test that Italian voice 'doppie' produces consistent output across multiple requests.

    This word was specifically reported in WET-163 as alternating between English and Italian.
    """
    port = tts_server
    url = f"http://127.0.0.1:{port}/v1/audio/speech"

    payload = {
        "model": "tts-1",
        "voice": ITALIAN_VOICE,
        "input": "doppie",
        "response_format": "mp3",
        "speed": 1,
    }

    hashes = []
    num_requests = 10

    for _ in range(num_requests):
        response = requests.post(url, json=payload, timeout=10)
        assert response.status_code == 200

        content_hash = hashlib.md5(response.content).hexdigest()
        hashes.append(content_hash)

    unique_hashes = set(hashes)
    if len(unique_hashes) != 1:
        hash_counts = {h: hashes.count(h) for h in unique_hashes}
        pytest.fail(
            f"Italian voice ({ITALIAN_VOICE}) 'doppie' is alternating! Found {len(unique_hashes)} different outputs:\n"
            + "\n".join(
                [
                    f"  {h}: {count} times ({count / num_requests * 100:.1f}%)"
                    for h, count in hash_counts.items()
                ]
            )
        )


def test_italian_voice_streaming_consistency(tts_server):
    """Test that Italian voice produces consistent output in streaming mode."""
    port = tts_server
    url = f"http://127.0.0.1:{port}/v1/audio/speech"

    payload = {
        "model": "tts-1",
        "voice": ITALIAN_VOICE,
        "input": "buongiorno",
        "response_format": "mp3",
        "speed": 1,
        "stream_format": "audio_stream",
    }

    hashes = []
    num_requests = 5

    for _ in range(num_requests):
        response = requests.post(url, json=payload, timeout=10, stream=True)
        assert response.status_code == 200

        chunks = []
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)

        content = b"".join(chunks)
        content_hash = hashlib.md5(content).hexdigest()
        hashes.append(content_hash)

    unique_hashes = set(hashes)
    assert len(unique_hashes) == 1, (
        f"Italian voice streaming is alternating! Found {len(unique_hashes)} different outputs. "
        f"Hashes: {unique_hashes}"
    )
