import os
import socket
import subprocess
import sys
import time
import tempfile
import requests
import json
import difflib
from pathlib import Path

import pytest

try:
    import whisper
except Exception:
    whisper = None

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
except Exception:
    Console = None

try:
    from diff_match_patch import diff_match_patch
except Exception:
    diff_match_patch = None


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
        time.sleep(0.2)
    return False


def progress(msg: str):
    """Write progress messages directly to the real terminal to bypass pytest capture."""
    try:
        sys.__stdout__.write(f"[e2e-test] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        # Fallback to regular stdout
        print(f"[e2e-test] {msg}")


def normalize_text(s: str) -> str:
    return " ".join(s.lower().split())


def side_by_side_diff(original: str, transcribed: str):
    """Produce a human-friendly side-by-side diff.

    Prefer `diff-match-patch` semantic cleanup when available and fall back
    to a word-level SequenceMatcher if not.
    """
    console = Console() if Console else None

    if diff_match_patch is not None:
        dmp = diff_match_patch()
        # Use the library to compute diffs and clean them up semantically
        diffs = dmp.diff_main(original, transcribed)
        dmp.diff_cleanupSemantic(diffs)

        # Build left/right strings with color markers
        left_parts = []
        right_parts = []
        for op, data in diffs:
            if op == 0:  # equal
                left_parts.append(data)
                right_parts.append(data)
            elif op == -1:  # deletion from original
                left_parts.append(("del", data))
            elif op == 1:  # insertion in transcribed
                right_parts.append(("ins", data))

        # If rich is available, build a table with styled text
        if console:
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Original", overflow="fold")
            table.add_column("Transcribed", overflow="fold")

            # Convert parts into single strings for display, preserving colored markers
            def render_parts(parts):
                out = Text()
                for part in parts:
                    if isinstance(part, tuple):
                        typ, txt = part
                        if typ == 'del':
                            out.append(txt, style="red")
                        elif typ == 'ins':
                            out.append(txt, style="green")
                    else:
                        out.append(part)
                return out

            left_text = render_parts(left_parts)
            right_text = render_parts(right_parts)
            table.add_row(left_text, right_text)
            console.print(table)
            return

        # Fallback: produce a simple unified diff text output
        left_str = ''.join(p if not isinstance(p, tuple) else '' for p in left_parts)
        right_str = ''.join(p if not isinstance(p, tuple) else '' for p in right_parts)
        for line in difflib.unified_diff(left_str.splitlines(), right_str.splitlines(), lineterm=""):
            print(line)
        return

    # Older fallback: word-level diff using SequenceMatcher (less human-friendly)
    orig_words = original.split()
    trans_words = transcribed.split()
    matcher = difflib.SequenceMatcher(None, orig_words, trans_words)
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Original", overflow="fold")
    table.add_column("Transcribed", overflow="fold")

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left = " ".join(orig_words[i1:i2])
        right = " ".join(trans_words[j1:j2])
        if tag == 'equal':
            table.add_row(left, right)
        elif tag == 'replace':
            left_text = Text(left, style="red")
            right_text = Text(right, style="green")
            table.add_row(left_text, right_text)
        elif tag == 'delete':
            left_text = Text(left, style="red")
            table.add_row(left_text, "")
        elif tag == 'insert':
            right_text = Text(right, style="green")
            table.add_row("", right_text)

    if console:
        console.print(table)
    else:
        for line in difflib.unified_diff(original.splitlines(), transcribed.splitlines(), lineterm=""):
            print(line)


@pytest.mark.slow
@pytest.mark.parametrize("input_text", [
    "Hello",
    "The quick brown fox jumps over the lazy dog.",
    "This is a paragraph. It has multiple sentences to exercise TTS and transcription quality.",
    (
        "This is an end-to-end TTS test. The quick brown fox jumps over the lazy dog. "
        "We repeat the sentence many times to generate a large input for TTS generation."
    ) * 10,
])
@pytest.mark.slow
def test_e2e_tts_whisper_local(input_text):
    if whisper is None:
        pytest.skip("whisper package not installed")

    port = get_free_port()

    env = os.environ.copy()
    env["PORT"] = str(port)
    # Disable API key requirement for test run
    env["REQUIRE_API_KEY"] = "false"

    # Start the server as a subprocess (inherit stdio so logs appear live)
    progress(f"starting server on port {port}")
    proc = subprocess.Popen([sys.executable, "-u", "app/server.py"], env=env, stdout=None, stderr=None)
    progress(f"server pid={proc.pid}")
    try:
        progress("waiting for server to be reachable...")
        assert wait_for_server(port, timeout=15.0), "Server did not start in time"
        progress("server is reachable")

        url = f"http://127.0.0.1:{port}/v1/audio/speech"
        headers = {"Content-Type": "application/json"}
        payload = {"input": input_text, "stream_format": "audio_stream"}

        progress(f"sending TTS request to {url} (streaming)")
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=120) as resp:
            resp.raise_for_status()

            # Write streamed audio to a temp file
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            try:
                total_bytes = 0
                last_report = time.time()
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        tmp.write(chunk)
                        total_bytes += len(chunk)
                        # periodic progress report
                        if time.time() - last_report > 2.0:
                            progress(f"received {total_bytes} bytes so far...")
                            last_report = time.time()
                tmp.flush()
                tmp.close()

                audio_path = tmp.name
                progress(f"saved streamed audio to {audio_path} ({total_bytes} bytes)")

                # Transcribe using whisper
                whisper_model_name = os.getenv("WHISPER_MODEL", "medium")
                progress(f"loading whisper model '{whisper_model_name}' (may take a while)...")
                model = whisper.load_model(whisper_model_name)
                progress("transcribing audio with whisper...")
                result = model.transcribe(audio_path)
                transcribed = result.get("text", "")
                progress("transcription completed")

                # Normalize and compare
                norm_orig = normalize_text(input_text)
                norm_trans = normalize_text(transcribed)

                ratio = difflib.SequenceMatcher(None, norm_orig, norm_trans).ratio()

                # Print side-by-side diff to aid debugging
                progress("--- Side by side diff ---")
                side_by_side_diff(norm_orig, norm_trans)
                progress(f"similarity ratio={ratio:.3f}")

                # Dynamic threshold for very short inputs
                threshold = 0.40 if len(input_text.split()) <= 3 else 0.60
                assert ratio >= threshold, f"Transcription similarity too low: {ratio:.2f} (threshold {threshold})"

            finally:
                # cleanup audio file
                try:
                    Path(tmp.name).unlink()
                except Exception:
                    pass

    finally:
        # Shutdown server gracefully
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        # Print server stderr for debugging if test failed
        stderr = proc.stderr.read().decode('utf-8', errors='ignore') if proc.stderr else ''
        stdout = proc.stdout.read().decode('utf-8', errors='ignore') if proc.stdout else ''
        if stderr:
            print("--- server stderr ---")
            print(stderr)
        if stdout:
            print("--- server stdout ---")
            print(stdout)


def test_safari_range_probe_short_tts():
    """Ensure the server responds to Safari-style Range probes with an accurate total for short TTS clips."""
    port = get_free_port()

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["REQUIRE_API_KEY"] = "false"

    progress(f"starting server on port {port} for Range-probe test")
    proc = subprocess.Popen([sys.executable, "-u", "app/server.py"], env=env, stdout=None, stderr=None)
    try:
        assert wait_for_server(port, timeout=15.0), "Server did not start in time"

        init_url = f"http://127.0.0.1:{port}/v1/audio/speech/init"
        payload = {"input": "aprile in Italia", "response_format": "mp3"}
        headers = {"Content-Type": "application/json"}
        r = requests.post(init_url, json=payload, headers=headers, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        assert "stream_id" in data and "token" in data, f"unexpected init response: {data}"

        stream_id = data["stream_id"]
        token = data["token"]
        stream_url = f"http://127.0.0.1:{port}/v1/audio/speech/stream/{stream_id}?token={token}"

        # Send Safari-like small range probe
        probe_headers = {"Range": "bytes=0-1"}
        probe = requests.get(stream_url, headers=probe_headers, timeout=15.0)
        # Server should return 206 Partial Content with Content-Range containing the actual total
        assert probe.status_code == 206, f"Expected 206 from probe, got {probe.status_code}"
        cr = probe.headers.get("Content-Range")
        assert cr and cr.startswith("bytes 0-1/"), f"Unexpected Content-Range header: {cr}"
        # Content-Length should match the returned body length
        cl = probe.headers.get("Content-Length")
        assert cl is not None and int(cl) == len(probe.content), "Content-Length header mismatch"

        # Now request the full stream and ensure we can download it
        with requests.get(stream_url, stream=True, timeout=60.0) as full_resp:
            full_resp.raise_for_status()
            total = 0
            for chunk in full_resp.iter_content(chunk_size=4096):
                if chunk:
                    total += len(chunk)
            assert total >= int(cr.split("/")[-1]), f"Full stream size {total} smaller than advertised total {cr}"

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
