"""
Test to verify Italian voice consistency bug (WET-163) is fixed.
The bug caused TTS to alternate between English and Italian for the same Italian voice.
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

    # Path to the server script
    project_root = Path(__file__).parent.parent
    server_script = project_root / "app" / "server.py"

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["API_KEY"] = "test_api_key"
    env["REQUIRE_API_KEY"] = "false"
    env["FLASK_DEBUG"] = "false"

    # Start the server
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait for server to be ready
    if not wait_for_server(port, timeout=15.0):
        proc.kill()
        out, err = proc.communicate(timeout=5.0)
        pytest.fail(f"Server did not start in time. stdout: {out}, stderr: {err}")

    yield port

    # Cleanup
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
        "voice": "it-IT-GiuseppeMultilingualNeural",
        "input": "ciao",
        "response_format": "mp3",
        "speed": 1
    }

    hashes = []
    num_requests = 5

    for i in range(num_requests):
        response = requests.post(url, json=payload, timeout=10)
        assert response.status_code == 200, f"Request {i+1} failed with status {response.status_code}"

        content = response.content
        content_hash = hashlib.md5(content).hexdigest()
        hashes.append(content_hash)

    # All hashes should be identical
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
        "voice": "it-IT-GiuseppeMultilingualNeural",
        "input": "doppie",
        "response_format": "mp3",
        "speed": 1
    }

    hashes = []
    sizes = []
    num_requests = 10

    for i in range(num_requests):
        response = requests.post(url, json=payload, timeout=10)
        assert response.status_code == 200, f"Request {i+1} failed with status {response.status_code}"

        content = response.content
        content_hash = hashlib.md5(content).hexdigest()
        hashes.append(content_hash)
        sizes.append(len(content))

    # All hashes should be identical
    unique_hashes = set(hashes)
    if len(unique_hashes) != 1:
        # Provide detailed failure information
        hash_counts = {h: hashes.count(h) for h in unique_hashes}
        pytest.fail(
            f"Italian voice 'doppie' is alternating! Found {len(unique_hashes)} different outputs:\n" +
            "\n".join([f"  {h}: {count} times ({count/num_requests*100:.1f}%)"
                      for h, count in hash_counts.items()])
        )


def test_italian_voice_streaming_consistency(tts_server):
    """Test that Italian voice produces consistent output in streaming mode."""
    port = tts_server
    url = f"http://127.0.0.1:{port}/v1/audio/speech"

    payload = {
        "model": "tts-1",
        "voice": "it-IT-GiuseppeMultilingualNeural",
        "input": "buongiorno",
        "response_format": "mp3",
        "speed": 1,
        "stream_format": "audio_stream"
    }

    hashes = []
    num_requests = 5

    for i in range(num_requests):
        response = requests.post(url, json=payload, timeout=10, stream=True)
        assert response.status_code == 200, f"Request {i+1} failed with status {response.status_code}"

        # Collect all chunks
        chunks = []
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                chunks.append(chunk)

        content = b''.join(chunks)
        content_hash = hashlib.md5(content).hexdigest()
        hashes.append(content_hash)

    # All hashes should be identical
    unique_hashes = set(hashes)
    assert len(unique_hashes) == 1, (
        f"Italian voice streaming is alternating! Found {len(unique_hashes)} different outputs. "
        f"Hashes: {unique_hashes}"
    )

