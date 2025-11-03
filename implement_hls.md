# HLS Implementation for iOS Audio Streaming

## Overview
HTTP Live Streaming (HLS) is Apple's native streaming protocol. iOS Safari supports HLS natively, which allows true progressive audio playback without waiting for the entire stream.

## What is HLS?
HLS requires:
1. **Audio Segmentation**: Split the audio stream into small chunks (typically 2-10 seconds each, in MP3 format)
2. **Playlist File**: Create an `.m3u8` file that lists all the segments
3. **Segment Serving**: Serve individual segment files (.mp3) that the browser can request as needed

## Implementation Options

### Option 1: Modify TTS Docker Service (Recommended)
Modify the `openai-edge-tts` service to support HLS:

**Current Flow:**
1. Frontend requests `/v1/audio/speech` → nginx proxies → TTS service
2. TTS service calls Azure TTS API, receives streaming audio chunks
3. TTS service streams chunks back through nginx to frontend

**Changes needed in TTS service:**
1. Add `stream_format=hls` parameter support
2. When HLS is requested:
   - TTS service receives audio chunks from Azure TTS (same as now)
   - **As chunks arrive**, segment them into HLS segments:
     - Buffer chunks until you have ~5-10 seconds of audio
     - Write segment file: `segments/{session_id}/segment001.mp3`
     - Update `.m3u8` playlist file: `segments/{session_id}/playlist.m3u8`
     - Continue as more chunks arrive
   - Return the playlist URL: `/v1/audio/speech/hls/{session_id}/playlist.m3u8`
   - Serve playlist and segments at these endpoints

**Key Point:** The TTS service **generates** the audio itself (from Azure TTS), so it has full control over the stream.

Flow for Option 1:
```
1. Frontend POST /v1/audio/speech with stream_format=hls
   ↓
2. Nginx proxies to TTS service
   ↓
3. TTS service calls Azure TTS API → receives streaming MP3 chunks
   ↓
4. TTS service buffers chunks → segments into HLS (as chunks arrive)
   ↓
5. TTS service creates/updates playlist.m3u8
   ↓
6. TTS service returns playlist URL immediately (before audio done)
   ↓
7. Frontend requests playlist.m3u8 → TTS serves it
   ↓
8. Browser requests segment001.mp3, segment002.mp3, etc. as needed
   ↓
9. TTS serves segments from its temporary storage
```

The TTS service can segment the audio **as it's being generated** because it controls the entire audio generation pipeline. It doesn't receive audio from elsewhere - it creates it.

**Pros:**
- Clean separation of concerns
- TTS service handles all audio generation AND segmentation
- Can reuse same logic for other clients
- No changes needed to nginx or Go backend
- Single source of truth for audio format

**Cons:**
- Need to modify/fork the Docker image (`mrwetsnow/openai-edge-tts`)
- More complex TTS service code
- Need to manage temporary segment storage

### Option 2: Backend Proxy with Segmenting
Add HLS support in the Go backend (but note: currently nginx proxies directly to TTS, so this would require nginx changes too):

**Current Flow Issue:**
- Currently nginx proxies `/v1/audio/` directly to TTS service
- Go backend doesn't intercept TTS requests

**Changes needed:**
1. **Modify nginx** to proxy TTS HLS requests through Go backend instead of directly to TTS
2. **Create new endpoint in Go backend**: `/v1/audio/speech/hls`
3. When requested:
   - Go backend fetches audio stream from TTS service (`stream_format=audio_stream`)
   - Buffer incoming audio chunks
   - Use ffmpeg (or Go audio library) to segment:
     - Accumulate ~5-10 seconds of audio
     - Write segment file to temporary storage
     - Update `.m3u8` playlist
   - Return `.m3u8` playlist URL
   - Serve segments at `/v1/audio/speech/hls/{session_id}/{segment}.mp3`

**Backend code structure:**
```go
// New endpoint
POST /v1/audio/speech/hls
{
  "input": "text to speak",
  "voice": "alloy",
  ...
}

Response: {
  "playlist_url": "/v1/audio/speech/hls/session123/playlist.m3u8"
}

// Segment serving endpoint
GET /v1/audio/speech/hls/{session_id}/{segment}.mp3
GET /v1/audio/speech/hls/{session_id}/playlist.m3u8
```

**Pros:**
- No need to modify TTS Docker service
- Full control over segmenting logic
- Can cache segments if needed

**Cons:**
- Need to add ffmpeg dependency or Go audio libraries
- Temporary file storage for segments
- More backend complexity

### Option 3: Client-Side Segmenting (Not Recommended)
Segment in the browser:
- **Not feasible** - browsers can't create valid MP3 segments from raw audio chunks
- Would require WebAssembly MP3 encoder, which is complex and large

## Recommended Implementation: Option 2

### Backend Changes Required

1. **Add HLS endpoint in `backend/internal/handlers/`**:
   - New handler: `audio_hls_handler.go`
   - Creates HLS session, streams from TTS service, segments audio

2. **Add audio segmenting library**:
   - Use `github.com/nareix/joy4` or similar Go library
   - Or shell out to `ffmpeg` for segmenting

3. **Temporary storage**:
   - Store segments in memory or temp directory
   - Clean up after session expires

4. **Update swagger.yaml**:
   - Add HLS endpoint definition
   - Add segment serving endpoints

5. **Frontend changes**:
   - Detect iOS Safari
   - Use HLS endpoint when on iOS
   - Point `<audio src>` to `.m3u8` playlist URL
   - Browser handles everything else automatically!

### Frontend Changes

In `streamingTTS.ts`:
```typescript
if (typeof MediaSource === 'undefined') {
  // iOS Safari - use HLS
  const response = await fetch('/v1/audio/speech/hls', {
    method: 'POST',
    body: JSON.stringify({ input: text, voice, ... })
  });
  const { playlist_url } = await response.json();

  globalAudioElement.src = playlist_url;
  globalAudioElement.load();
  await globalAudioElement.play();
} else {
  // Desktop - use MediaSource
  // ... existing code
}
```

### Example .m3u8 Playlist Format

```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:5.0,
segment001.mp3
#EXTINF:5.0,
segment002.mp3
#EXTINF:3.5,
segment003.mp3
#EXT-X-ENDLIST
```

## Benefits
- ✅ True progressive playback on iOS
- ✅ No gaps or interruptions
- ✅ Browser handles buffering automatically
- ✅ Works with pause/resume
- ✅ Low latency start

## Implementation Complexity
- **Backend**: Medium (need segmenting logic, temp storage, cleanup)
- **Frontend**: Low (just switch endpoint on iOS)
- **Testing**: Medium (need to test on iOS devices)

## Alternative: Simpler Approach
If full HLS is too complex, we could:
1. Create larger initial blob (50-100KB) for iOS
2. Still wait for stream to complete, but start playback earlier
3. Accept that iOS will buffer more before starting

This is simpler but less optimal than true HLS streaming.

