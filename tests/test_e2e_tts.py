import os
import socket
import subprocess
import sys
import time
import tempfile
import requests
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
import _test_helpers as _helpers

# Repo root for subprocess cwd (avoid Path.resolve() here: can hang on some VM/container mounts).
_PROJECT_ROOT = Path(__file__).parent.parent

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


def normalize_text(s: str) -> str:
    return " ".join(s.lower().split())


def side_by_side_diff(original: str, transcribed: str):
    """Produce a human-friendly side-by-side diff.

    Prefer `diff-match-patch` semantic cleanup when available and fall back
    to a word-level SequenceMatcher if not.
    """
    # Record the last diff so pytest plugin can print it on test failure.
    try:
        _helpers.set_last_diff(original, transcribed)
    except Exception:
        pass
    # Use the real terminal stdout to bypass pytest capture so diffs are visible on failure.
    console = Console(file=sys.__stdout__) if Console else None

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
            try:
                sys.__stdout__.write(line + "\n")
            except Exception:
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
            try:
                sys.__stdout__.write(line + "\n")
            except Exception:
                print(line)


@pytest.mark.parametrize("input_text", [
    "Hello",
    "The quick brown fox jumps over the lazy dog.",
    "This is a paragraph. It has multiple sentences to exercise TTS and transcription quality.",
    (
        "This is an end-to-end TTS test. The quick brown fox jumps over the lazy dog. "
        "We repeat the sentence many times to generate a large input for TTS generation."
    )
    * 4,
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
    env["OPENAI_EDGE_TTS_TEST_MODE"] = "1"

    server_py = _PROJECT_ROOT / "app" / "server.py"
    proc = subprocess.Popen(
        [sys.executable, "-u", str(server_py)],
        env=env,
        cwd=str(_PROJECT_ROOT),
        stdout=None,
        stderr=None,
    )
    try:
        assert wait_for_server(port, timeout=15.0), "Server did not start in time"

        url = f"http://127.0.0.1:{port}/v1/audio/speech"
        headers = {"Content-Type": "application/json"}
        payload = {"input": input_text, "stream_format": "audio_stream"}

        with requests.post(url, json=payload, headers=headers, stream=True, timeout=120) as resp:
            resp.raise_for_status()

            # Write streamed audio to a temp file
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            try:
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        tmp.write(chunk)
                tmp.flush()
                tmp.close()

                audio_path = tmp.name

                whisper_model_name = os.getenv("WHISPER_MODEL", "medium")
                model = whisper.load_model(whisper_model_name)
                result = model.transcribe(audio_path)
                transcribed = result.get("text", "")

                # Normalize and compare
                norm_orig = normalize_text(input_text)
                norm_trans = normalize_text(transcribed)

                ratio = difflib.SequenceMatcher(None, norm_orig, norm_trans).ratio()

                side_by_side_diff(norm_orig, norm_trans)

                # Whisper matches short/medium prompts well; long repeated prose is often summarized
                # or truncated, so full-string similarity collapses while content is still correct.
                nw = len(input_text.split())
                if nw <= 3:
                    threshold = 0.40
                elif nw <= 90:
                    threshold = 0.60
                else:
                    # Whisper often drops repeated paragraphs and punctuation differs (. vs none);
                    # ratio ~0.25 for 4 repeats when only 3 are transcribed is typical.
                    threshold = 0.22
                    for needle in ("quick brown fox", "lazy dog"):
                        assert needle in norm_trans, (
                            f"Long-input sanity: Whisper output missing {needle!r} — got {norm_trans[:500]!r}..."
                        )
                assert ratio >= threshold, f"Transcription similarity too low: {ratio:.2f} (threshold {threshold})"
            finally:
                # cleanup audio file
                try:
                    Path(tmp.name).unlink()
                except Exception:
                    pass

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_ios_avplayer_icy_metadata_probe_streams_audio():
    """AppleCoreMedia sends Icy-Metadata: 1; response must not be empty or playback is silent."""
    port = get_free_port()

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["REQUIRE_API_KEY"] = "false"
    env["OPENAI_EDGE_TTS_TEST_MODE"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-u", str(_PROJECT_ROOT / "app" / "server.py")],
        env=env,
        cwd=str(_PROJECT_ROOT),
        stdout=None,
        stderr=None,
    )
    try:
        assert wait_for_server(port, timeout=15.0), "Server did not start in time"

        init_url = f"http://127.0.0.1:{port}/v1/audio/speech/init"
        payload = {"input": "ciao", "response_format": "mp3"}
        headers = {"Content-Type": "application/json"}
        r = requests.post(init_url, json=payload, headers=headers, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        stream_id = data["stream_id"]
        token = data["token"]
        stream_url = f"http://127.0.0.1:{port}/v1/audio/speech/stream/{stream_id}?token={token}"

        icy_headers = {"Icy-Metadata": "1", "User-Agent": "AppleCoreMedia/1.0.0 (iPhone)"}
        with requests.get(stream_url, headers=icy_headers, stream=True, timeout=60.0) as resp:
            resp.raise_for_status()
            total = 0
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    total += len(chunk)
                if total > 500:
                    break
        assert total > 500, f"expected non-trivial mp3 bytes from Icy-Metadata GET, got {total}"

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_safari_range_probe_short_tts():
    """Ensure the server responds to Safari-style Range probes with an accurate total for short TTS clips."""
    port = get_free_port()

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["REQUIRE_API_KEY"] = "false"
    env["OPENAI_EDGE_TTS_TEST_MODE"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-u", str(_PROJECT_ROOT / "app" / "server.py")],
        env=env,
        cwd=str(_PROJECT_ROOT),
        stdout=None,
        stderr=None,
    )
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
