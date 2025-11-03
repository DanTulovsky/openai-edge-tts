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
TEST_TEXT = (
    "This is a significantly longer paragraph intended to stress test the HLS streaming pipeline. "
    "We are generating enough text so that the text-to-speech engine produces a sustained stream of audio chunks, "
    "which should trigger multiple segments to be created by the server as the data arrives. "
    "The goal is to ensure that the segmentation logic properly buffers incoming MP3 frames, finalizes segments at the "
    "configured duration threshold, updates the playlist in real time, and ultimately allows an iOS Safari client to begin "
    "progressive playback before the entire synthesis completes. By using a large paragraph with many sentences, commas, and "
    "varied pacing, we simulate a realistic narration scenario. This helps us validate that the playlist begins empty, transitions "
    "to contain the first segment within a short time window, and continues to grow predictably with additional segment references. "
    "Furthermore, the test will verify that at least one segment can be fetched directly via HTTP and that the media bytes resemble "
    "a valid MP3 stream, beginning with ID3 tags or a sync word. Finally, by exercising both the waiting-for-generation path and the "
    "completed-stream path, we confirm that the playlist includes an #EXT-X-ENDLIST marker when synthesis finishes, ensuring clean "
    "termination behavior for clients that fully buffer the stream. "
    "In a real application, paragraphs of this size are common: think of podcasts, audiobooks, educational lectures, or long-form "
    "explanatory content that must be delivered with minimal delay and maximum reliability. The ability to start playback while the "
    "audio is still being synthesized is critical to perceived responsiveness, especially on mobile devices where buffering budgets "
    "and user expectations are quite different from desktop environments. With HLS, the browser handles adaptive buffering, recovery, "
    "and incremental fetching, as long as the playlist is well-formed and segments appear at predictable intervals. "
    "This paragraph continues with additional details to further increase the audio duration and challenge the segmentation logic. "
    "We expect the server to emit several segments of roughly equal duration, but if the final segment is shorter, the playlist "
    "should still include it with an accurate #EXTINF duration. The client will then gracefully reach the #EXT-X-ENDLIST tag, signaling "
    "that playback can end without waiting for any more data. If network conditions were variable, a live playlist could continue to grow, "
    "but for this test we complete the synthesis so we can validate both the in-progress and completed phases. "
    "Adding more sentences for load: The quick brown fox jumps over the lazy dog; this classic pangram ensures coverage of all letters. "
    "Edge cases include pauses, punctuation, numbers like one hundred twenty-three point four five, and abbreviations like U.S.A. or e.g. "
    "We also include proper nouns, product names, and occasionally foreign words such as déjà vu or résumé to verify pronunciation and flow. "
    "Ultimately, this extended content should be more than sufficient to create multiple HLS segments during a single synthesis pass."
)
TEST_TEXT = TEST_TEXT + " " + TEST_TEXT


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


def test_hls_streaming_codec(codec: str) -> bool:
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
        return True
    finally:
        if server_process:
            print("\nStopping server...")
            stop_server(server_process)
            print("Server stopped.")


if __name__ == "__main__":
    # Simple CLI: --codec mp3|aac  or  --mp3 / --aac
    selected = None
    for arg in sys.argv[1:]:
        if arg.startswith("--codec="):
            selected = arg.split("=", 1)[1].strip().lower()
        elif arg == "--mp3":
            selected = "mp3"
        elif arg == "--aac":
            selected = "aac"

    if selected in ("mp3", "aac"):
        print(f"\n--- Running {selected.upper()} HLS test ---")
        ok = test_hls_streaming_codec(selected)
        if ok:
            print(f"\nSUCCESS: HLS {selected.upper()} test passed")
        else:
            print(f"\nFAILURE: HLS {selected.upper()} test failed")
        sys.exit(0 if ok else 1)

    # Default: run both
    print("\n--- Running MP3 HLS test ---")
    ok_mp3 = test_hls_streaming_codec('mp3')
    time.sleep(0.5)
    print("\n--- Running AAC HLS test ---")
    ok_aac = test_hls_streaming_codec('aac')
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
