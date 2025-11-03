#!/usr/bin/env python3
"""
Test script for HLS streaming functionality.
Starts the server, sends an HLS request, and validates the response.

Requirements:
    pip install requests
"""

import os
import sys
import time
import json
import tempfile
import shutil
import string
from difflib import SequenceMatcher
import subprocess
import errno
try:
    import requests
except ImportError:
    print("ERROR: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)
import signal
import atexit
import socket
from pathlib import Path
from urllib.parse import urljoin

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent / "app"))

TEST_API_KEY = "test_api_key"
# Long test text omitted for brevity in this header; it remains defined below

_server_process = None


def _kill_process_group(proc: subprocess.Popen):
    if not proc:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def stop_server(server_process):
    """Stop the server process."""
    _kill_process_group(server_process)


def _free_port(port: int):
    """Best-effort: kill any process listening on the given port (macOS/Linux)."""
    try:
        result = subprocess.run(["bash", "-lc", f"lsof -ti tcp:{port}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
        pids = [pid for pid in result.stdout.strip().splitlines() if pid]
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
    except Exception:
        pass


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def start_server(port: int):
    """Start the TTS server on the given port."""
    global _server_process
    _free_port(port)

    env = os.environ.copy()
    env['PORT'] = str(port)
    env['API_KEY'] = TEST_API_KEY
    env['DEBUG_STREAMING'] = 'False'

    server_process = subprocess.Popen(
        [sys.executable, "app/server.py"],
        env=env,
        stdout=None,
        stderr=None,
        cwd=Path(__file__).parent,
        preexec_fn=os.setsid
    )
    _server_process = server_process

    atexit.register(lambda: stop_server(_server_process))

    def _sig_handler(signum, frame):
        stop_server(server_process)
        sys.exit(130)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    print("Waiting for server to start...")
    base_url = f"http://localhost:{port}"
    max_attempts = 30
    for _ in range(max_attempts):
        try:
            response = requests.get(f"{base_url}/v1/models", timeout=1)
            if response.status_code == 200:
                print("Server is ready!")
                return server_process, base_url
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)

    print("Server failed to start within timeout period")
    stop_server(server_process)
    raise RuntimeError("Server failed to start")


# (TEST_TEXT definition remains the same)
TEST_TEXT = """
There was something in the sky. What exactly was up there wasn't immediately clear. But there was definitely something in the sky and it was getting bigger and bigger.
There was a time when this wouldn't have bothered her. The fact that it did actually bother her bothered her even more. What had changed in her life that such a small thing could annoy her so much for the entire day? She knew it was ridiculous that she even took notice of it, yet she was still obsessing over it as she tried to fall asleep.
She considered the birds to be her friends. She'd put out food for them each morning and then she'd watch as they came to the feeders to gorge themselves for the day. She wondered what they would do if something ever happened to her. Would they miss the meals she provided if she failed to put out the food one morning?
What were they eating? It didn't taste like anything she had ever eaten before and although she was famished, she didn't dare ask. She knew the answer would be one she didn't want to hear.
"""


def _segment_matcher(codec: str):
    if codec.lower() == 'aac':
        return ('.m4a', '.m4s', '.ts', '.mp4')
    return ('.mp3',)


def _line_has_segment(line: str, codec: str) -> bool:
    if not line or line.strip().startswith('#'):
        return False
    exts = _segment_matcher(codec)
    low = line.lower()
    return any(ext in low for ext in exts) or 'segment' in low


def _normalize_text(s: str) -> str:
    table = str.maketrans('', '', string.punctuation)
    return ' '.join(s.lower().translate(table).split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return True
    except Exception:
        return False


def _remux_playlist_to_wav(playlist_url: str, wav_path: Path) -> bool:
    if not _ffmpeg_available():
        print("   ERROR: ffmpeg is required for transcription verification but was not found in PATH")
        return False
    try:
        # Remux/convert HLS playlist to mono 16k WAV for ASR
        cmd = [
            "ffmpeg", "-y", "-i", playlist_url,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(wav_path)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            print("   ERROR: ffmpeg failed to export WAV for transcription")
            if proc.stderr:
                print(proc.stderr.splitlines()[-1])
            return False
        return True
    except Exception as e:
        print(f"   ERROR: Exception while running ffmpeg: {e}")
        return False


def _transcribe_wav(wav_path: Path, model_name: str = "base") -> str:
    try:
        import whisper  # type: ignore
    except Exception:
        print("   ERROR: Python package 'whisper' is required. Install with: pip install openai-whisper")
        return ""
    try:
        model = whisper.load_model(model_name)
        result = model.transcribe(str(wav_path))
        text = result.get("text", "") or ""
        return text.strip()
    except Exception as e:
        print(f"   ERROR: Whisper transcription failed: {e}")
        return ""


def _print_side_by_side_diff(ref: str, hyp: str):
    """Render a colorized side-by-side word diff using libraries.

    - Uses diff-match-patch for word-level diffing
    - Uses rich to render a two-column, full-width table with colors
    Falls back to unified diff if dependencies are unavailable.
    """
    try:
        from diff_match_patch import diff_match_patch  # type: ignore
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text

        console = Console()
        # Prepare word-level inputs
        ref_words = ref.split()
        hyp_words = hyp.split()
        ref_join = "\n".join(ref_words) + "\n"
        hyp_join = "\n".join(hyp_words) + "\n"

        dmp = diff_match_patch()
        # Use line-mode via linesToChars to treat each word as a line
        chars1, chars2, line_array = dmp.diff_linesToChars(ref_join, hyp_join)
        diffs = dmp.diff_main(chars1, chars2, False)
        dmp.diff_cleanupSemantic(diffs)
        # In diff-match-patch, diff_charsToLines mutates the diffs list in place (returns None)
        dmp.diff_charsToLines(diffs, line_array)

        left_text = Text()
        right_text = Text()
        for op, chunk in diffs:
            words = [w for w in chunk.split("\n") if w]
            if op == 0:  # EQUAL
                if words:
                    left_text.append(" ".join(words) + " ")
                    right_text.append(" ".join(words) + " ")
            elif op == -1:  # DELETE (in ref only)
                if words:
                    left_text.append(" ".join(words) + " ", style="bold red")
            elif op == 1:  # INSERT (in hyp only)
                if words:
                    right_text.append(" ".join(words) + " ", style="yellow")

        # Build side-by-side table using full terminal width
        table = Table(expand=True, show_header=True, header_style="bold", pad_edge=False)
        table.add_column("EXPECTED", ratio=1)
        table.add_column("TRANSCRIPT", ratio=1)
        table.add_row(left_text, right_text)

        console.print("\n   --- Diff (normalized) ---")
        console.print(table)
    except Exception:
        # Fallback to stdlib unified diff
        import difflib
        ref_lines = [w + "\n" for w in ref.split()]
        hyp_lines = [w + "\n" for w in hyp.split()]
        print("\n   --- Diff (normalized, fallback) ---")
        for line in difflib.unified_diff(ref_lines, hyp_lines, fromfile='EXPECTED', tofile='TRANSCRIPT'):
            print("   " + line.rstrip())


def test_hls_streaming_codec(codec: str, verify_transcription: bool = False, transcribe_threshold: float = 0.75, transcribe_model: str = "base") -> bool:
    server_process = None
    try:
        port = _get_free_port()
        server_process, base_url = start_server(port)
        print("\n" + "="*60)
        print(f"Testing HLS Streaming ({codec})")
        print("="*60)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TEST_API_KEY}"
        }
        payload = {
            "input": TEST_TEXT,
            "voice": "en-US-AriaNeural",
            "response_format": codec,
            "stream_format": "hls",
            "hls_segment_duration": 3.0
        }
        print(f"\n1. Sending HLS request with text: '{TEST_TEXT[:50]}...'")
        response = requests.post(
            f"{base_url}/v1/audio/speech",
            headers=headers,
            json=payload,
            timeout=10
        )
        print(f"   Response status: {response.status_code}")
        if response.status_code != 200:
            print(f"   Error: {response.text}")
            return False

        result = response.json()
        playlist_url = result.get("playlist_url")
        if not playlist_url:
            print(f"   Error: No playlist_url in response: {result}")
            return False
        full_playlist_url = urljoin(base_url, playlist_url)
        print(f"   Playlist URL: {full_playlist_url}")

        print(f"\n2. Fetching initial playlist (must be valid on first response)...")
        try:
            playlist_response = requests.get(full_playlist_url, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"   ERROR: Failed to fetch initial playlist: {e}")
            return False
        status = playlist_response.status_code
        content_type = playlist_response.headers.get('Content-Type', '')
        playlist_content = playlist_response.text if status == 200 else playlist_response.text
        if status != 200:
            print(f"   ERROR: Initial playlist status was {status}, body: {playlist_content[:200]}")
            return False
        if 'application/vnd.apple.mpegurl' not in content_type:
            print(f"   ERROR: Initial playlist content-type was {content_type}, expected application/vnd.apple.mpegurl")
            return False
        if not playlist_content.startswith('#EXTM3U'):
            print("   ERROR: Initial playlist does not start with #EXTM3U")
            return False
        segment_lines_now = [
            line for line in playlist_content.split('\n')
            if _line_has_segment(line, codec)
        ]
        if not segment_lines_now:
            print("   ERROR: Initial playlist has no segment references")
            return False
        if '#EXT-X-ENDLIST' in playlist_content:
            print("   ERROR: Initial playlist already contains ENDLIST (should be in-progress)")
            return False
        print("   Initial playlist is valid and contains at least one segment.")

        first_segment_now = segment_lines_now[0].strip()
        session_id = playlist_url.split('/')[-2]
        immediate_seg_url = urljoin(base_url, f"/v1/audio/speech/hls/{session_id}/{first_segment_now}")
        print(f"   dl:first url={immediate_seg_url}")
        seg_resp_now = requests.get(immediate_seg_url, timeout=10)
        if seg_resp_now.status_code != 200 or len(seg_resp_now.content) == 0:
            print("   err:first-seg empty-or-failed")
            return False
        print("   dl:first ok")

        print(f"\n3a. Simulating playback until completion (polling for new segments)...")
        downloaded_segments = set([first_segment_now])
        endlist_seen = False
        start_time = time.time()
        max_play_time = 120.0
        poll_interval = 0.5
        import sys as _sys
        _sys.stdout.write("   segs:")
        _sys.stdout.flush()
        while time.time() - start_time < max_play_time:
            try:
                pl_resp = requests.get(full_playlist_url, timeout=5)
            except requests.exceptions.RequestException:
                _sys.stdout.write(" !")
                _sys.stdout.flush()
                time.sleep(poll_interval)
                continue
            if pl_resp.status_code != 200:
                _sys.stdout.write(" ?")
                _sys.stdout.flush()
                time.sleep(poll_interval)
                continue
            pl_text = pl_resp.text
            if '#EXT-X-ENDLIST' in pl_text:
                endlist_seen = True
            seg_lines_now = [line for line in pl_text.split('\n') if _line_has_segment(line, codec)]
            for seg_name in seg_lines_now:
                seg_name = seg_name.strip()
                if seg_name in downloaded_segments:
                    continue
                seg_url = urljoin(base_url, f"/v1/audio/speech/hls/{session_id}/{seg_name}")
                try:
                    seg_resp = requests.get(seg_url, timeout=10)
                    if seg_resp.status_code == 200 and len(seg_resp.content) > 0:
                        downloaded_segments.add(seg_name)
                        _sys.stdout.write(f" {len(downloaded_segments)}")
                        _sys.stdout.flush()
                    else:
                        _sys.stdout.write(" x")
                        _sys.stdout.flush()
                        return False
                except requests.exceptions.RequestException:
                    _sys.stdout.write(" e")
                    _sys.stdout.flush()
                    return False
            if endlist_seen:
                _sys.stdout.write(" END\n")
                _sys.stdout.flush()
                break
            time.sleep(poll_interval)
        if not endlist_seen:
            print("\n   ERROR: ENDLIST was not observed within timeout while simulating playback")
            return False

        # Basic final playlist checks
        if 'application/vnd.apple.mpegurl' not in playlist_response.headers.get('Content-Type', ''):
            print("   ERROR: Final playlist is not served with application/vnd.apple.mpegurl")
            return False
        if not playlist_content.startswith('#EXTM3U'):
            print("   ERROR: Final playlist does not start with #EXTM3U")
            return False
        success = True

        # If requested, verify transcription BEFORE stopping the server
        if success and verify_transcription:
            try:
                print("\n4. Verifying transcription against input text...")
                with tempfile.TemporaryDirectory() as td:
                    wav_path = Path(td) / "capture.wav"
                    if not _remux_playlist_to_wav(full_playlist_url, wav_path):
                        return False
                    transcript = _transcribe_wav(wav_path, model_name=transcribe_model)
                    if not transcript:
                        print("   ERROR: Empty transcription result")
                        return False
                    norm_ref = _normalize_text(TEST_TEXT)
                    norm_hyp = _normalize_text(transcript)
                    score = _similarity(norm_ref, norm_hyp)
                    print(f"   Transcription similarity: {score:.3f} (threshold {transcribe_threshold:.2f})")
                    if score < transcribe_threshold:
                        print("   ERROR: Transcription similarity below threshold")
                        _print_side_by_side_diff(norm_ref, norm_hyp)
                        return False
                    print("   Transcription verification passed")
            except Exception as e:
                print(f"   ERROR: Transcription verification failed: {e}")
                return False
        return True
    finally:
        if server_process:
            print("\nStopping server...")
            stop_server(server_process)
            print("Server stopped.")
    # Should not reach here; success path returns above
    return False


if __name__ == "__main__":
    # Simple CLI: --codec mp3|aac  or  --mp3 / --aac
    selected = None
    verify_transcription = False
    transcribe_threshold = 0.75
    transcribe_model = os.getenv("HLS_TEST_TRANSCRIBE_MODEL", "base")
    for arg in sys.argv[1:]:
        if arg.startswith("--codec="):
            selected = arg.split("=", 1)[1].strip().lower()
        elif arg == "--mp3":
            selected = "mp3"
        elif arg == "--aac":
            selected = "aac"
        elif arg == "--verify-transcription":
            verify_transcription = True
        elif arg.startswith("--transcribe-threshold="):
            try:
                transcribe_threshold = float(arg.split("=", 1)[1])
            except Exception:
                pass
        elif arg.startswith("--transcribe-model="):
            transcribe_model = arg.split("=", 1)[1].strip()

    if selected in ("mp3", "aac"):
        print(f"\n--- Running {selected.upper()} HLS test ---")
        ok = test_hls_streaming_codec(selected, verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
        if ok:
            print(f"\nSUCCESS: HLS {selected.upper()} test passed")
        else:
            print(f"\nFAILURE: HLS {selected.upper()} test failed")
        sys.exit(0 if ok else 1)

    # Default: run both
    print("\n--- Running MP3 HLS test ---")
    ok_mp3 = test_hls_streaming_codec('mp3', verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
    time.sleep(0.5)
    print("\n--- Running AAC HLS test ---")
    ok_aac = test_hls_streaming_codec('aac', verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
    if ok_mp3:
        print("\nSUCCESS: HLS MP3 test passed")
    else:
        print("\nFAILURE: HLS MP3 test failed")
    if ok_aac:
        print("SUCCESS: HLS AAC test passed")
    else:
        print("FAILURE: HLS AAC test failed")
    overall_ok = ok_mp3 and ok_aac
    if overall_ok:
        print("\nSUCCESS: All HLS tests passed")
    else:
        print("\nFAILURE: One or more HLS tests failed")
    sys.exit(0 if overall_ok else 1)
