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
def test_e2e_tts_whisper_local():
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

        # Build a large text payload
        base = (
            "This is an end-to-end TTS test. The quick brown fox jumps over the lazy dog. "
            "We repeat the sentence many times to generate a large input for TTS generation. "
        )
        large_text = base * 10  # make it large

        url = f"http://127.0.0.1:{port}/v1/audio/speech"
        headers = {"Content-Type": "application/json"}
        payload = {"input": large_text, "stream_format": "audio_stream"}

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
                progress("loading whisper model 'small' (may take a while)...")
                model = whisper.load_model("small")
                progress("transcribing audio with whisper...")
                result = model.transcribe(audio_path)
                transcribed = result.get("text", "")
                progress("transcription completed")

                # Normalize and compare
                norm_orig = normalize_text(large_text)
                norm_trans = normalize_text(transcribed)

                ratio = difflib.SequenceMatcher(None, norm_orig, norm_trans).ratio()

                # Print side-by-side diff to aid debugging
                progress("--- Side by side diff ---")
                side_by_side_diff(norm_orig, norm_trans)
                progress(f"similarity ratio={ratio:.3f}")

                # Assert transcription reasonably matches (allow some errors)
                assert ratio >= 0.60, f"Transcription similarity too low: {ratio:.2f}"

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
