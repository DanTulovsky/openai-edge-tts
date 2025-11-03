# hls_handler.py

import os
import uuid
import threading
import time
import tempfile
import subprocess
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

from tts_handler import is_ffmpeg_installed, generate_speech_stream
from utils import DETAILED_ERROR_LOGGING, DEBUG_STREAMING
from config import DEFAULT_CONFIGS

# HLS Configuration
HLS_SEGMENT_DURATION = float(os.getenv('HLS_SEGMENT_DURATION', DEFAULT_CONFIGS.get('HLS_SEGMENT_DURATION', 5.0)))
HLS_CLEANUP_TIMEOUT = int(os.getenv('HLS_CLEANUP_TIMEOUT', DEFAULT_CONFIGS.get('HLS_CLEANUP_TIMEOUT', 300)))


class HLSSession:
    """Manages an HLS streaming session with segments and playlist (MP3 segments)."""

    def __init__(self, session_id: str, segment_duration: float = HLS_SEGMENT_DURATION):
        self.session_id = session_id
        self.segment_duration = segment_duration
        self.temp_dir = tempfile.TemporaryDirectory()
        self.segment_dir = Path(self.temp_dir.name)
        self.playlist_path = self.segment_dir / "playlist.m3u8"
        self.current_segment_buffer = []
        self.current_segment_duration = 0.0
        self.segment_counter = 0
        self.lock = threading.Lock()
        self.completed = False
        self.error = None
        self.created_at = datetime.now()
        self.playlist_written = False

        # Initialize playlist
        self._initialize_playlist()

    def _initialize_playlist(self):
        """Create initial HLS playlist file."""
        playlist_content = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            f"#EXT-X-TARGETDURATION:{int(self.segment_duration) + 1}\n"
            "#EXT-X-PLAYLIST-TYPE:EVENT\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n"
        )
        with open(self.playlist_path, 'w') as f:
            f.write(playlist_content)
        self.playlist_written = True

    def add_audio_chunk(self, chunk: bytes):
        """Add an audio chunk to the current segment buffer."""
        should_finalize = False
        with self.lock:
            if self.completed or self.error:
                return

            self.current_segment_buffer.append(chunk)
            chunk_count = len(self.current_segment_buffer)

            # Estimate duration based on chunk size (rough estimate for MP3)
            estimated_duration = len(chunk) / (128 * 1000 / 8)  # bytes per second at 128kbps
            self.current_segment_duration += estimated_duration

            # Finalize segment if threshold reached or safety chunk cap
            if self.current_segment_duration >= self.segment_duration:
                should_finalize = True
            elif chunk_count >= 50:
                if DEBUG_STREAMING:
                    print(f"[DEBUG_STREAMING] Forcing segment creation after {chunk_count} chunks (estimate={self.current_segment_duration:.2f}s)")
                should_finalize = True

        if should_finalize:
            self._finalize_segment()

    def _finalize_segment(self):
        """Write the current segment buffer to a file and update playlist."""
        with self.lock:
            if not self.current_segment_buffer:
                if DEBUG_STREAMING:
                    print(f"[DEBUG_STREAMING] _finalize_segment called with empty buffer")
                return
            buffer_to_write = list(self.current_segment_buffer)
            buffer_size_bytes = sum(len(chunk) for chunk in buffer_to_write)
            self.current_segment_buffer = []
            self.current_segment_duration = 0.0
            self.segment_counter += 1
            segment_counter = self.segment_counter
        if DEBUG_STREAMING:
            print(f"[DEBUG_STREAMING] Creating segment {segment_counter}: {len(buffer_to_write)} chunks, {buffer_size_bytes} bytes")
        segment_filename = f"segment{segment_counter:03d}.mp3"
        segment_path = self.segment_dir / segment_filename
        with open(segment_path, 'wb') as f:
            for chunk in buffer_to_write:
                f.write(chunk)
        actual_duration = self._get_segment_duration(segment_path)
        self._update_playlist(segment_filename, actual_duration)

    def _get_segment_duration(self, segment_path: Path) -> float:
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(segment_path)
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=5)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired) as e:
            if DETAILED_ERROR_LOGGING:
                print(f"Warning: Could not get segment duration via ffprobe: {e}. Using estimate.")
            file_size = segment_path.stat().st_size
            estimated = file_size / (192 * 128 / 8)
            return max(estimated, self.current_segment_duration) if self.current_segment_duration > 0 else estimated

    def _update_playlist(self, segment_filename: str, duration: float):
        with self.lock:
            playlist_lines = []
            if self.playlist_path.exists():
                with open(self.playlist_path, 'r') as f:
                    playlist_lines = [line.rstrip() for line in f.readlines()]
                    if playlist_lines and playlist_lines[-1].strip() == '#EXT-X-ENDLIST':
                        playlist_lines.pop()
            target_duration = int(self.segment_duration) + 1
            for i, line in enumerate(playlist_lines):
                if line.startswith('#EXT-X-TARGETDURATION:'):
                    current_target = int(line.split(':')[1])
                    if int(duration) + 1 > current_target:
                        target_duration = int(duration) + 1
                        playlist_lines[i] = f'#EXT-X-TARGETDURATION:{target_duration}'
            playlist_lines.append(f'#EXTINF:{duration:.3f},')
            playlist_lines.append(segment_filename)
            with open(self.playlist_path, 'w') as f:
                f.write('\n'.join(playlist_lines) + '\n')

    def finalize(self):
        with self.lock:
            if self.completed:
                return
            has_buffer = len(self.current_segment_buffer) > 0
            buffer_size = len(self.current_segment_buffer)
        if has_buffer:
            if DEBUG_STREAMING:
                print(f"[DEBUG_STREAMING] Finalizing with remaining buffer: {buffer_size} chunks")
            self._finalize_segment()
        with self.lock:
            if self.playlist_path.exists():
                with open(self.playlist_path, 'a') as f:
                    f.write('#EXT-X-ENDLIST\n')
            if self.segment_counter == 0:
                if DEBUG_STREAMING or DETAILED_ERROR_LOGGING:
                    print(f"[WARNING] HLS session finalized with no segments created! session_id={self.session_id}")
            self.completed = True

    def set_error(self, error: str):
        with self.lock:
            self.error = error
            self.finalize()

    def get_segment_path(self, segment_filename: str) -> Optional[Path]:
        segment_path = self.segment_dir / segment_filename
        return segment_path if segment_path.exists() else None

    def cleanup(self):
        try:
            self.temp_dir.cleanup()
        except Exception as e:
            if DETAILED_ERROR_LOGGING:
                print(f"Error cleaning up HLS session {self.session_id}: {e}")


class HLSSessionFFmpeg:
    """HLS session that uses ffmpeg to produce fMP4 AAC segments and playlist in real-time."""

    def __init__(self, session_id: str, segment_duration: float = HLS_SEGMENT_DURATION):
        if not is_ffmpeg_installed():
            raise RuntimeError("FFmpeg required for AAC HLS")
        self.session_id = session_id
        self.segment_duration = segment_duration
        self.temp_dir = tempfile.TemporaryDirectory()
        self.segment_dir = Path(self.temp_dir.name)
        self.playlist_path = self.segment_dir / "playlist.m3u8"
        self.lock = threading.Lock()
        self.completed = False
        self.error = None
        self.created_at = datetime.now()
        self.ffmpeg_proc: Optional[subprocess.Popen] = None
        self._start_ffmpeg()

    @property
    def segment_counter(self) -> int:
        """Return the number of generated fMP4 segment files (segment*.m4s)."""
        try:
            return len(list(self.segment_dir.glob('segment*.m4s')))
        except Exception:
            return 0

    def _start_ffmpeg(self):
        # Build ffmpeg HLS fMP4 pipeline reading MP3 chunks from stdin and writing .m4s segments
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', 'mp3', '-i', 'pipe:0',
            '-c:a', 'aac', '-b:a', '128k',
            '-f', 'hls',
            '-hls_time', str(self.segment_duration),
            '-hls_playlist_type', 'event',
            '-hls_segment_type', 'fmp4',
            '-hls_fmp4_init_filename', 'init.m4a',
            '-hls_segment_filename', str(self.segment_dir / 'segment%03d.m4s'),
            str(self.playlist_path)
        ]
        os.makedirs(self.segment_dir, exist_ok=True)
        self.ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=self.segment_dir)

    def add_audio_chunk(self, chunk: bytes):
        if not self.ffmpeg_proc or not self.ffmpeg_proc.stdin:
            return
        try:
            self.ffmpeg_proc.stdin.write(chunk)
            self.ffmpeg_proc.stdin.flush()
        except Exception as e:
            self.set_error(str(e))

    def finalize(self):
        try:
            if self.ffmpeg_proc and self.ffmpeg_proc.stdin:
                try:
                    self.ffmpeg_proc.stdin.close()
                except Exception:
                    pass
            if self.ffmpeg_proc:
                self.ffmpeg_proc.wait(timeout=10)
        except Exception as e:
            self.set_error(str(e))
        finally:
            with self.lock:
                self.completed = True

    def set_error(self, error: str):
        with self.lock:
            self.error = error

    def get_segment_path(self, segment_filename: str) -> Optional[Path]:
        p = self.segment_dir / segment_filename
        return p if p.exists() else None

    def cleanup(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass


# Global session storage
_sessions: Dict[str, object] = {}
_sessions_lock = threading.Lock()


def create_hls_session(segment_duration: float = HLS_SEGMENT_DURATION, codec: str = 'mp3') -> str:
    """Create a new HLS session and return session ID."""
    session_id = str(uuid.uuid4())
    if codec.lower() == 'aac':
        session = HLSSessionFFmpeg(session_id, segment_duration)
    else:
        session = HLSSession(session_id, segment_duration)
    with _sessions_lock:
        _sessions[session_id] = session
    return session_id


def get_hls_session(session_id: str):
    with _sessions_lock:
        return _sessions.get(session_id)


def generate_hls_stream(text: str, voice: str, speed: float, session_id: str):
    session = get_hls_session(session_id)
    if not session:
        if DEBUG_STREAMING:
            print(f"[DEBUG_STREAMING] generate_hls_stream: Session not found - session_id={session_id}")
        return
    try:
        if DEBUG_STREAMING:
            start_time = datetime.now()
            print(f"[DEBUG_STREAMING] generate_hls_stream: Starting HLS generation - session_id={session_id}, text_length={len(text)}, timestamp={start_time}")
        for chunk in generate_speech_stream(text, voice, speed):
            current_session = get_hls_session(session_id)
            if not current_session:
                if DEBUG_STREAMING:
                    print(f"[DEBUG_STREAMING] generate_hls_stream: Session cleaned up - session_id={session_id}")
                break
            current_session.add_audio_chunk(chunk)
        final_session = get_hls_session(session_id)
        if final_session:
            final_session.finalize()
            if DEBUG_STREAMING:
                end_time = datetime.now()
                total_delta = (end_time - start_time).total_seconds()
                print(f"[DEBUG_STREAMING] generate_hls_stream: Completed - session_id={session_id}, total_time={total_delta:.3f}s")
    except Exception as e:
        error_msg = str(e)
        if DETAILED_ERROR_LOGGING:
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
        err_sess = get_hls_session(session_id)
        if err_sess:
            err_sess.set_error(error_msg)


def cleanup_old_sessions():
    """Clean up sessions older than HLS_CLEANUP_TIMEOUT seconds."""
    current_time = datetime.now()
    sessions_to_remove = []

    with _sessions_lock:
        for session_id, session in list(_sessions.items()):
            age = (current_time - session.created_at).total_seconds()
            if age > HLS_CLEANUP_TIMEOUT:
                sessions_to_remove.append(session_id)

        for session_id in sessions_to_remove:
            session = _sessions.pop(session_id, None)
            if session:
                session.cleanup()
                if DEBUG_STREAMING:
                    print(f"[DEBUG_STREAMING] Cleaned up old HLS session: {session_id}")

    return len(sessions_to_remove)


def start_cleanup_thread():
    """Start a background thread to periodically clean up old sessions."""
    def cleanup_loop():
        while True:
            time.sleep(60)  # Check every minute
            cleanup_old_sessions()

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    return cleanup_thread
