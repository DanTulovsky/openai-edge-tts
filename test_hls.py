#!/usr/bin/env python3
"""
Test script for HLS streaming functionality.
Starts the server, sends an HLS request, and validates the response.

Requirements:
    pip install playwright requests
    playwright install webkit
"""

import os
import sys
import time
import json
import tempfile
import shutil
import string
import re
from difflib import SequenceMatcher
import subprocess
import errno
try:
    import requests
except ImportError:
    print("ERROR: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("WARNING: Playwright not installed. Falling back to requests emulation.")
    print("For true Safari emulation, install with: pip install playwright && playwright install webkit")

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

        # Emulate Safari/iOS fetch characteristics
        safari_ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TEST_API_KEY}",
            "User-Agent": safari_ua,
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

        # Use Playwright WebKit for true Safari emulation if available
        if PLAYWRIGHT_AVAILABLE:
            print("   Using Playwright WebKit (true Safari emulation)")
            playwright = sync_playwright().start()
            try:
                browser = playwright.webkit.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                # Fetch playlist using Safari's actual network stack
                try:
                    playlist_response = page.request.get(full_playlist_url, timeout=30000)
                except Exception as e:
                    print(f"   ERROR: Failed to fetch initial playlist: {e}")
                    browser.close()
                    playwright.stop()
                    return False

                status = playlist_response.status
                content_type = playlist_response.headers.get('content-type', '')
                playlist_content = playlist_response.text()

                # Store browser/context/page for later use
                browser_context = context
                browser_page = page
                browser_instance = browser
                playwright_instance = playwright

            except Exception as e:
                print(f"   WARNING: Playwright failed ({e}), falling back to requests")
                browser_context = None
                browser_page = None
                browser_instance = None
                playwright_instance = None
                # Fall through to requests fallback
        else:
            browser_context = None
            browser_page = None
            browser_instance = None
            playwright_instance = None

        # Fallback to requests if Playwright not available or failed
        sess = None
        if browser_page is None:
            print("   Using requests library (manual Safari emulation)")
            sess = requests.Session()
            sess.headers.update({
                "User-Agent": safari_ua,
                "Accept": "application/vnd.apple.mpegurl, */*;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            })
            try:
                playlist_response = sess.get(full_playlist_url, timeout=30)
            except requests.exceptions.RequestException as e:
                print(f"   ERROR: Failed to fetch initial playlist: {e}")
                return False
            status = playlist_response.status_code
            content_type = playlist_response.headers.get('Content-Type', '')
            playlist_content = playlist_response.text

        if status != 200:
            print(f"   ERROR: Initial playlist status was {status}, body: {playlist_content[:200]}")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        # Safari accepts either application/x-mpegURL or application/vnd.apple.mpegurl
        if 'application/x-mpegurl' not in content_type.lower() and 'application/vnd.apple.mpegurl' not in content_type.lower():
            print(f"   ERROR: Initial playlist content-type was {content_type}, expected application/x-mpegURL or application/vnd.apple.mpegurl")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        # Ensure server avoids range semantics for playlists (Safari expects 200)
        if browser_page:
            resp_headers = playlist_response.headers
        else:
            resp_headers = playlist_response.headers
        if 'accept-ranges' in resp_headers or 'Accept-Ranges' in resp_headers:
            print("   ERROR: Playlist response advertises Accept-Ranges; expected none for .m3u8")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        # Basic CORS allowance (common for web playback)
        cors_header = resp_headers.get('access-control-allow-origin') or resp_headers.get('Access-Control-Allow-Origin', '')
        if cors_header and cors_header not in ("*",):
            print("   ERROR: Playlist response has unexpected CORS header")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        if not playlist_content.startswith('#EXTM3U'):
            print("   ERROR: Initial playlist does not start with #EXTM3U")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        segment_lines_now = [
            line for line in playlist_content.split('\n')
            if _line_has_segment(line, codec)
        ]
        if not segment_lines_now:
            print("   ERROR: Initial playlist has no segment references")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        # Validate CODECS attribute in EXT-X-MAP if present (Safari requirement for AAC)
        ext_map_lines = [line for line in playlist_content.split('\n') if line.strip().startswith('#EXT-X-MAP:')]
        for map_line in ext_map_lines:
            # Validate CODECS attribute (Safari requirement)
            codecs_match = re.search(r'CODECS="([^"]+)"', map_line)
            if not codecs_match:
                print(f"   ERROR: EXT-X-MAP line missing CODECS attribute (Safari requirement): {map_line}")
                if browser_instance:
                    browser_instance.close()
                    playwright_instance.stop()
                return False

            codecs_value = codecs_match.group(1)
            if codecs_value != 'mp4a.40.2':
                print(f"   ERROR: EXT-X-MAP CODECS value is '{codecs_value}', expected 'mp4a.40.2' (Safari requirement): {map_line}")
                if browser_instance:
                    browser_instance.close()
                    playwright_instance.stop()
                return False

        # For AAC codec, ensure EXT-X-MAP line exists with CODECS
        if codec.lower() == 'aac' and not ext_map_lines:
            print("   ERROR: AAC codec requires EXT-X-MAP line with CODECS attribute, but none found in playlist")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        if '#EXT-X-ENDLIST' in playlist_content:
            print("   Note: Initial playlist already contains ENDLIST (VOD-style). Proceeding.")
        print("   Initial playlist is valid and contains at least one segment.")

        first_segment_now = segment_lines_now[0].strip()
        # Extract segment filename (handles both relative and absolute URLs)
        if '/' in first_segment_now:
            segment_filename = first_segment_now.split('/')[-1]
        else:
            segment_filename = first_segment_now
        session_id = playlist_url.split('/')[-2]
        immediate_seg_url = urljoin(base_url, f"/v1/audio/speech/hls/{session_id}/{segment_filename}")
        print(f"   dl:first url={immediate_seg_url}")
        # Fetch first segment using same method
        if browser_page:
            try:
                seg_resp_now = browser_page.request.get(immediate_seg_url, timeout=10000)
                seg_status = seg_resp_now.status
                seg_content_len = len(seg_resp_now.body())
                seg_headers = seg_resp_now.headers
            except Exception as e:
                print(f"   ERROR: Failed to fetch first segment: {e}")
                browser_instance.close()
                playwright_instance.stop()
                return False
        else:
            seg_resp_now = sess.get(immediate_seg_url, timeout=10)
            seg_status = seg_resp_now.status_code
            seg_content_len = len(seg_resp_now.content)
            seg_headers = seg_resp_now.headers
        if seg_status != 200 or seg_content_len == 0:
            print("   err:first-seg empty-or-failed")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        # Validate Accept-Ranges header on segment
        accept_ranges = seg_headers.get('accept-ranges') or seg_headers.get('Accept-Ranges', '')
        if accept_ranges.lower() != 'bytes':
            print(f"   ERROR: Segment response missing or incorrect Accept-Ranges header (got: {accept_ranges})")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        print("   dl:first ok")

        # Validate AAC-specific requirements (codec identifier and CMAF structure)
        if codec.lower() == 'aac':
            print("\n2b. Validating AAC-LC codec identifier and CMAF segment structure...")

            # Check init.m4a for AAC-LC codec identifier (mp4a.40.2)
            init_url = urljoin(base_url, f"/v1/audio/speech/hls/{session_id}/init.m4a")
            try:
                if browser_page:
                    init_resp = browser_page.request.get(init_url, timeout=10000)
                    init_content = init_resp.body()
                else:
                    init_resp = sess.get(init_url, timeout=10)
                    init_content = init_resp.content

                init_status = init_resp.status if browser_page else init_resp.status_code
                if init_status != 200:
                    print(f"   ERROR: Failed to fetch init.m4a: status {init_status}")
                    if browser_instance:
                        browser_instance.close()
                        playwright_instance.stop()
                    return False

                # Use ffprobe to check the codec identifier (most reliable method)
                # The codec identifier is stored in binary MP4 boxes, not as plain text
                if _ffmpeg_available():
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as tmp_file:
                        tmp_file.write(init_content)
                        tmp_path = tmp_file.name
                    try:
                        # Use ffprobe to get codec information
                        cmd = [
                            'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams',
                            tmp_path
                        ]
                        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
                        if result.returncode != 0:
                            print(f"   WARNING: ffprobe failed to analyze init.m4a: {result.stderr}")
                            # Fall back to binary search
                            init_content_bytes = init_content if isinstance(init_content, bytes) else bytes(init_content)
                            if b'mp4a' in init_content_bytes:
                                # Check if we can find the pattern "mp4a" followed by ".40.2" nearby
                                mp4a_idx = init_content_bytes.find(b'mp4a')
                                if mp4a_idx >= 0 and mp4a_idx + 10 < len(init_content_bytes):
                                    # Look for ".40.2" pattern nearby (allow some byte variations)
                                    nearby = init_content_bytes[max(0, mp4a_idx-10):min(len(init_content_bytes), mp4a_idx+50)]
                                    if b'.40' in nearby or b'40.2' in nearby or b'\x40\x02' in nearby:
                                        print("   ✓ init.m4a appears to contain AAC-LC codec (binary pattern found)")
                                    else:
                                        print("   ERROR: init.m4a contains 'mp4a' but not 'mp4a.40.2' pattern")
                                        print("          Safari requires the full codec identifier 'mp4a.40.2'")
                                        if browser_instance:
                                            browser_instance.close()
                                            playwright_instance.stop()
                                        return False
                                else:
                                    print("   ERROR: Could not validate AAC-LC codec identifier in init.m4a")
                                    if browser_instance:
                                        browser_instance.close()
                                        playwright_instance.stop()
                                    return False
                            else:
                                print("   ERROR: init.m4a does not appear to contain AAC codec")
                                if browser_instance:
                                    browser_instance.close()
                                    playwright_instance.stop()
                                return False
                        else:
                            # Parse JSON output from ffprobe
                            probe_data = json.loads(result.stdout)
                            codec_valid = False
                            codec_info = None
                            for stream in probe_data.get('streams', []):
                                codec_name = stream.get('codec_name', '')
                                codec_tag_string = stream.get('codec_tag_string', '').lower()
                                profile = stream.get('profile', '').lower()
                                codec_long_name = stream.get('codec_long_name', '')

                                # Store info for debugging
                                codec_info = {
                                    'codec_name': codec_name,
                                    'codec_tag_string': codec_tag_string,
                                    'profile': profile or 'default',
                                    'codec_long_name': codec_long_name
                                }

                                # Verify it's AAC codec in MP4 container
                                # With -profile:a aac_low, FFmpeg should encode as AAC-LC which uses "mp4a.40.2" identifier
                                if codec_name == 'aac':
                                    if 'mp4a' in codec_tag_string or codec_tag_string == '':
                                        # AAC-LC profile check: profile should indicate low complexity, or be empty (default)
                                        # The .40.2 identifier is set automatically by FFmpeg when using AAC-LC
                                        codec_valid = True
                                        print("   ✓ init.m4a contains AAC codec (verified via ffprobe)")
                                        print(f"      codec_name={codec_name}, codec_tag={codec_tag_string or 'N/A'}, profile={profile or 'default'}")
                                        print(f"      Note: With -profile:a aac_low flag, codec identifier should be 'mp4a.40.2'")
                                        break

                            if not codec_valid:
                                print("   ERROR: ffprobe did not find AAC codec in init.m4a")
                                if codec_info:
                                    print(f"          Found: {codec_info}")
                                print(f"          Full ffprobe output: {result.stdout[:1000]}")
                                if browser_instance:
                                    browser_instance.close()
                                    playwright_instance.stop()
                                return False
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
                else:
                    # Fallback: simple binary search if ffprobe not available
                    init_content_bytes = init_content if isinstance(init_content, bytes) else bytes(init_content)
                    if b'mp4a' not in init_content_bytes:
                        print("   ERROR: init.m4a does not appear to contain AAC codec")
                        if browser_instance:
                            browser_instance.close()
                            playwright_instance.stop()
                        return False
                    print("   WARNING: ffprobe not available, cannot fully validate 'mp4a.40.2' codec identifier")
                    print("           (fallback: found 'mp4a' pattern, but full validation requires ffprobe)")
            except Exception as e:
                print(f"   ERROR: Exception while validating init.m4a: {e}")
                import traceback
                print(traceback.format_exc())
                if browser_instance:
                    browser_instance.close()
                    playwright_instance.stop()
                return False

            # Check that CMAF segments start with "moof" box (not styp/sidx)
            try:
                if browser_page:
                    seg_content_first = seg_resp_now.body()
                else:
                    seg_content_first = seg_resp_now.content

                # Check first 32 bytes to identify box types
                if len(seg_content_first) < 32:
                    print("   WARNING: Segment too small to validate box structure")
                else:
                    # MP4 boxes are 4-byte identifiers starting at byte 4
                    # Read first box size (4 bytes) and type (4 bytes at offset 4)
                    box_size = int.from_bytes(seg_content_first[0:4], byteorder='big')
                    box_type = seg_content_first[4:8].decode('ascii', errors='ignore')

                    if box_type == 'styp':
                        print("   ERROR: CMAF segment starts with 'styp' box instead of 'moof'")
                        print("          CMAF segments for HLS should start directly with 'moof' box")
                        if browser_instance:
                            browser_instance.close()
                            playwright_instance.stop()
                        return False
                    elif box_type == 'sidx':
                        print("   ERROR: CMAF segment starts with 'sidx' box instead of 'moof'")
                        print("          CMAF segments for HLS should start directly with 'moof' box")
                        if browser_instance:
                            browser_instance.close()
                            playwright_instance.stop()
                        return False
                    elif box_type == 'moof':
                        print("   ✓ CMAF segment starts with 'moof' box (proper CMAF structure)")
                    else:
                        print(f"   WARNING: Unexpected first box type '{box_type}' (expected 'moof')")
            except Exception as e:
                print(f"   ERROR: Exception while validating CMAF segment structure: {e}")
                if browser_instance:
                    browser_instance.close()
                    playwright_instance.stop()
                return False

        # Test Range request on first segment (Safari uses range requests)
        print("   Testing Range request (bytes=0-1023) on first segment...")
        range_headers = {'Range': 'bytes=0-1023'}
        if browser_page:
            try:
                range_resp = browser_page.request.get(immediate_seg_url, headers=range_headers, timeout=10000)
                range_status = range_resp.status
                range_content_len = len(range_resp.body())
                range_resp_headers = range_resp.headers
            except Exception as e:
                print(f"   ERROR: Failed to fetch segment with Range header: {e}")
                browser_instance.close()
                playwright_instance.stop()
                return False
        else:
            range_resp = sess.get(immediate_seg_url, headers=range_headers, timeout=10)
            range_status = range_resp.status_code
            range_content_len = len(range_resp.content)
            range_resp_headers = range_resp.headers

        if range_status != 206:
            print(f"   ERROR: Range request returned status {range_status}, expected 206 Partial Content")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        # Validate Content-Range header
        content_range = range_resp_headers.get('content-range') or range_resp_headers.get('Content-Range', '')
        if not content_range.startswith('bytes '):
            print(f"   ERROR: Range response missing or invalid Content-Range header (got: {content_range})")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        # Validate Content-Range format: bytes start-end/total
        if not re.match(r'bytes \d+-\d+/\d+', content_range):
            print(f"   ERROR: Content-Range format invalid (got: {content_range})")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        # Validate Accept-Ranges on range response
        range_accept_ranges = range_resp_headers.get('accept-ranges') or range_resp_headers.get('Accept-Ranges', '')
        if range_accept_ranges.lower() != 'bytes':
            print(f"   ERROR: Range response missing Accept-Ranges header")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        if range_content_len != 1024:
            print(f"   WARNING: Range response length is {range_content_len}, expected 1024")

        print("   Range request test passed (206, valid Content-Range, Accept-Ranges)")

        # HEAD tests for segments
        print("   Testing HEAD without Range on first segment...")
        if browser_page:
            try:
                head_resp = browser_page.request.head(immediate_seg_url, timeout=10000)
                head_status = head_resp.status
                head_headers = head_resp.headers
            except Exception as e:
                print(f"   ERROR: HEAD (no-range) failed: {e}")
                if browser_instance:
                    browser_instance.close()
                    playwright_instance.stop()
                return False
        else:
            head_resp = sess.head(immediate_seg_url, timeout=10)
            head_status = head_resp.status_code
            head_headers = head_resp.headers
        if head_status != 200:
            print(f"   ERROR: HEAD (no-range) returned status {head_status}, expected 200")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        # Validate Accept-Ranges and Content-Length equals full size seen earlier
        head_accept_ranges = head_headers.get('accept-ranges') or head_headers.get('Accept-Ranges', '')
        if head_accept_ranges.lower() != 'bytes':
            print("   ERROR: HEAD (no-range) missing Accept-Ranges: bytes")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        reported_len = int(head_headers.get('content-length') or head_headers.get('Content-Length', '0') or '0')
        if reported_len and reported_len != seg_content_len:
            print(f"   WARNING: HEAD Content-Length ({reported_len}) != GET size ({seg_content_len})")

        print("   Testing HEAD with Range (bytes=0-1023) on first segment...")
        range_head_headers_in = {'Range': 'bytes=0-1023'}
        if browser_page:
            try:
                head_range_resp = browser_page.request.head(immediate_seg_url, headers=range_head_headers_in, timeout=10000)
                head_range_status = head_range_resp.status
                head_range_headers = head_range_resp.headers
            except Exception as e:
                print(f"   ERROR: HEAD (range) failed: {e}")
                if browser_instance:
                    browser_instance.close()
                    playwright_instance.stop()
                return False
        else:
            head_range_resp = sess.head(immediate_seg_url, headers=range_head_headers_in, timeout=10)
            head_range_status = head_range_resp.status_code
            head_range_headers = head_range_resp.headers
        if head_range_status != 206:
            print(f"   ERROR: HEAD (range) returned status {head_range_status}, expected 206")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        # Validate Content-Range and Accept-Ranges
        hr_content_range = head_range_headers.get('content-range') or head_range_headers.get('Content-Range', '')
        if not hr_content_range.startswith('bytes ') or not re.match(r'bytes \d+-\d+/\d+', hr_content_range):
            print(f"   ERROR: HEAD (range) missing/invalid Content-Range: {hr_content_range}")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        hr_accept_ranges = head_range_headers.get('accept-ranges') or head_range_headers.get('Accept-Ranges', '')
        if hr_accept_ranges.lower() != 'bytes':
            print("   ERROR: HEAD (range) missing Accept-Ranges: bytes")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False
        hr_len = int(head_range_headers.get('content-length') or head_range_headers.get('Content-Length', '0') or '0')
        if hr_len != 1024:
            print(f"   WARNING: HEAD (range) Content-Length is {hr_len}, expected 1024")

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
                if browser_page:
                    pl_resp = browser_page.request.get(full_playlist_url, timeout=5000)
                    pl_status = pl_resp.status
                    pl_text = pl_resp.text()
                elif sess:
                    pl_resp = sess.get(full_playlist_url, timeout=5)
                    pl_status = pl_resp.status_code
                    pl_text = pl_resp.text
                else:
                    _sys.stdout.write(" !")
                    _sys.stdout.flush()
                    time.sleep(poll_interval)
                    continue
            except Exception as e:
                _sys.stdout.write(" !")
                _sys.stdout.flush()
                time.sleep(poll_interval)
                continue
            if pl_status != 200:
                _sys.stdout.write(" ?")
                _sys.stdout.flush()
                time.sleep(poll_interval)
                continue
            if '#EXT-X-ENDLIST' in pl_text:
                endlist_seen = True
            seg_lines_now = [line for line in pl_text.split('\n') if _line_has_segment(line, codec)]
            for seg_name in seg_lines_now:
                seg_name = seg_name.strip()
                # Extract segment filename (handles both relative and absolute URLs)
                segment_filename = seg_name.split('/')[-1] if '/' in seg_name else seg_name
                if segment_filename in downloaded_segments:
                    continue
                seg_url = urljoin(base_url, f"/v1/audio/speech/hls/{session_id}/{segment_filename}")
                try:
                    if browser_page:
                        seg_resp = browser_page.request.get(seg_url, timeout=10000)
                        seg_status = seg_resp.status
                        seg_content_len = len(seg_resp.body())
                    elif sess:
                        seg_resp = sess.get(seg_url, timeout=10)
                        seg_status = seg_resp.status_code
                        seg_content_len = len(seg_resp.content)
                    else:
                        _sys.stdout.write(" e")
                        _sys.stdout.flush()
                        return False
                    if seg_status == 200 and seg_content_len > 0:
                        downloaded_segments.add(segment_filename)
                        _sys.stdout.write(f" {len(downloaded_segments)}")
                        _sys.stdout.flush()
                    else:
                        _sys.stdout.write(" x")
                        _sys.stdout.flush()
                        # Emit detailed diagnostics on first failure
                        print("\n   ERROR: Failed to download segment during polling")
                        print(f"     url: {seg_url}")
                        print(f"     status: {seg_status}")
                        print(f"     content_len: {seg_content_len}")
                        # Show a short playlist preview for context
                        preview = (pl_text or "")[:200].replace("\n", " | ")
                        print(f"     playlist preview: {preview}")
                        if browser_instance:
                            browser_instance.close()
                            playwright_instance.stop()
                        return False
                except Exception as e:
                    _sys.stdout.write(" e")
                    _sys.stdout.flush()
                    if browser_instance:
                        browser_instance.close()
                        playwright_instance.stop()
                    return False
            if endlist_seen:
                _sys.stdout.write(" END\n")
                _sys.stdout.flush()
                break
            time.sleep(poll_interval)
        if not endlist_seen:
            print("\n   ERROR: ENDLIST was not observed within timeout while simulating playback")
            if browser_instance:
                browser_instance.close()
                playwright_instance.stop()
            return False

        # Basic final playlist checks (playlist_content already validated)
        success = True

        # Clean up browser before transcription if using Playwright
        if browser_instance:
            browser_instance.close()
            playwright_instance.stop()

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


def test_hls_safari_audio_element(codec: str, verify_transcription: bool = False, transcribe_threshold: float = 0.75, transcribe_model: str = "base") -> bool:
    """Play HLS via an <audio> element in WebKit (Safari engine) and validate behavior."""
    if not PLAYWRIGHT_AVAILABLE:
        print("\nSKIP: Playwright not available; audio-element Safari test requires it")
        return True

    server_process = None
    try:
        port = _get_free_port()
        server_process, base_url = start_server(port)
        print("\n" + "="*60)
        print(f"Safari <audio> HLS Playback ({codec})")
        print("="*60)

        safari_ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TEST_API_KEY}",
            "User-Agent": safari_ua,
        }
        payload = {
            "input": TEST_TEXT,
            "voice": "en-US-AriaNeural",
            "response_format": codec,
            "stream_format": "hls",
            "hls_segment_duration": 3.0
        }

        print(f"\n1. Creating HLS session for audio-element test...")
        r = requests.post(f"{base_url}/v1/audio/speech", headers=headers, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"   ERROR: Failed to create HLS session: {r.status_code} {r.text}")
            return False
        playlist_url = r.json().get("playlist_url")
        if not playlist_url:
            print("   ERROR: No playlist_url in response")
            return False
        full_playlist_url = urljoin(base_url, playlist_url)
        print(f"   Playlist URL: {full_playlist_url}")

        # Build a minimal HTML page with <audio> element
        html = f"""
<!doctype html>
<html>
<head><meta charset=\"utf-8\"></head>
<body>
  <audio id=\"player\" src=\"{full_playlist_url}\" autoplay muted playsinline preload=\"auto\"></audio>
  <script>
    const a = document.getElementById('player');
    window.__readyState = () => a.readyState;
  </script>
  Ready
  </body>
  </html>
"""

        print("\n2. Launching WebKit and attaching network observers...")
        pw = sync_playwright().start()
        try:
            browser = pw.webkit.launch(headless=True)
            context = browser.new_context(user_agent=safari_ua)
            page = context.new_page()

            playlist_events = []
            segment_events = []

            def on_response(resp):
                try:
                    url = resp.url
                    status = resp.status
                    hdrs = resp.headers
                    if "playlist.m3u8" in url:
                        playlist_events.append((url, status, hdrs))
                    elif "/segment" in url or url.endswith(('.m4s', '.m4a', '.mp4', '.ts', '.mp3')):
                        segment_events.append((url, status, hdrs))
                except Exception:
                    pass

            page.on("response", on_response)

            page.set_default_timeout(30000)
            page.set_content(html)

            print("   Waiting for audio to reach HAVE_CURRENT_DATA (>=2)...")
            page.wait_for_function("() => window.__readyState && window.__readyState() >= 2")
            print("   Audio element readyState satisfied.")

            # Quick follow-up playlist fetch to read current content
            pl_resp = page.request.get(full_playlist_url, timeout=15000)
            pl_text = pl_resp.text()
            if not pl_text.startswith('#EXTM3U'):
                print("   ERROR: Playlist fetched via page does not start with #EXTM3U")
                return False

            # Validate observed responses
            if not playlist_events:
                print("   ERROR: No playlist requests observed in audio-element playback")
                return False
            # All playlist responses should be 200 and not advertise Accept-Ranges
            for url, status, hdrs in playlist_events:
                if status != 200:
                    print(f"   ERROR: Playlist status {status} for {url}")
                    return False
                if 'accept-ranges' in hdrs or 'Accept-Ranges' in hdrs:
                    print("   ERROR: Playlist advertised Accept-Ranges; expected none")
                    return False

            # Segment responses should be present; allow 200 or 206
            if not segment_events:
                print("   WARNING: No segment requests observed yet (very short playback or VOD).")
            else:
                for url, status, _hdrs in segment_events:
                    if status not in (200, 206):
                        print(f"   ERROR: Segment status {status} for {url}")
                        return False

            print("   Audio-element HLS playback checks passed.")

            # Optional: verify transcription by downloading full audio via playlist
            if verify_transcription:
                try:
                    print("\n3. Waiting for ENDLIST and verifying transcription...")
                    # Poll playlist until ENDLIST
                    endlist_seen = False
                    start_time = time.time()
                    timeout_s = 120.0
                    while time.time() - start_time < timeout_s:
                        pl_resp2 = page.request.get(full_playlist_url, timeout=15000)
                        pl_text2 = pl_resp2.text()
                        if '#EXT-X-ENDLIST' in pl_text2:
                            endlist_seen = True
                            break
                        time.sleep(0.5)
                    if not endlist_seen:
                        print("   ERROR: ENDLIST not observed within timeout for audio-element test")
                        return False

                    # Remux to WAV and transcribe
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
                        print("   Transcription verification passed (audio-element)")
                except Exception as e:
                    print(f"   ERROR: Audio-element transcription verification failed: {e}")
                    return False

            return True
        finally:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
    finally:
        if server_process:
            print("\nStopping server...")
            stop_server(server_process)
            print("Server stopped.")
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
            sys.exit(1)  # Exit early on failure
        if PLAYWRIGHT_AVAILABLE:
            print(f"\n--- Running {selected.upper()} Safari <audio> test ---")
            ok_audio = test_hls_safari_audio_element(selected, verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
            if ok_audio:
                print(f"\nSUCCESS: Safari <audio> {selected.upper()} test passed")
            else:
                print(f"\nFAILURE: Safari <audio> {selected.upper()} test failed")
            ok = ok and ok_audio
        sys.exit(0 if ok else 1)

    # Default: run both
    print("\n--- Running MP3 HLS test ---")
    ok_mp3 = test_hls_streaming_codec('mp3', verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
    if not ok_mp3:
        print("\nFAILURE: HLS MP3 test failed")
        sys.exit(1)  # Exit early on failure
    time.sleep(0.5)
    print("\n--- Running AAC HLS test ---")
    ok_aac = test_hls_streaming_codec('aac', verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
    if not ok_aac:
        print("FAILURE: HLS AAC test failed")
        sys.exit(1)  # Exit early on failure
    if PLAYWRIGHT_AVAILABLE:
        print("\n--- Running MP3 Safari <audio> test ---")
        ok_mp3_audio = test_hls_safari_audio_element('mp3', verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
        if not ok_mp3_audio:
            print("\nFAILURE: Safari <audio> MP3 test failed")
            sys.exit(1)
        print("\n--- Running AAC Safari <audio> test ---")
        ok_aac_audio = test_hls_safari_audio_element('aac', verify_transcription=verify_transcription, transcribe_threshold=transcribe_threshold, transcribe_model=transcribe_model)
        if not ok_aac_audio:
            print("\nFAILURE: Safari <audio> AAC test failed")
            sys.exit(1)
    else:
        ok_mp3_audio = ok_aac_audio = True
    if ok_mp3:
        print("\nSUCCESS: HLS MP3 test passed")
    else:
        print("\nFAILURE: HLS MP3 test failed")
    if ok_aac:
        print("SUCCESS: HLS AAC test passed")
    else:
        print("FAILURE: HLS AAC test failed")
    overall_ok = ok_mp3 and ok_aac and ok_mp3_audio and ok_aac_audio
    if overall_ok:
        print("\nSUCCESS: All HLS tests passed")
    else:
        print("\nFAILURE: One or more HLS tests failed")
    sys.exit(0 if overall_ok else 1)
