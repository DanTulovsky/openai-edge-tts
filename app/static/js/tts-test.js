// Use exact import structure from OpenAI docs (from https://github.com/openai/openai-node)
// Import from jsdelivr with proper paths
// Using local OpenAI-compatible endpoint via fetch to /v1/audio/speech

// Global audio element for stop functionality
let currentAudioElement = null;
let currentMediaSource = null;
let currentSourceBuffer = null;
let isPlaying = false;
let shouldStop = false;
let streamReader = null;

// Detect iOS Safari
function isIOSSafari() {
    const ua = navigator.userAgent;
    const isIOS = /iPad|iPhone|iPod/.test(ua);
    const isSafari = /^((?!chrome|android).)*safari/i.test(ua);
    return isIOS || (isSafari && typeof MediaSource === 'undefined');
}

// Progressive audio streaming using MediaSource API (desktop) or HLS (iOS Safari)
// Starts playing as soon as the first chunk arrives
async function playAudioWithStop(response) {
    // Stop any existing playback
    stopCurrentPlayback();
    shouldStop = false;
    isPlaying = false;

    console.log('Setting up progressive audio streaming');

    // Check if response has a streaming body
    if (response.body && typeof response.body.getReader === 'function') {
        const contentType = response.headers.get('content-type') || 'audio/aac';

        // Map content types to MediaSource-compatible MIME types
        const mimeTypeMap = {
            'audio/mpeg': 'audio/mpeg',
            'audio/mp3': 'audio/mpeg',
            'audio/aac': 'audio/mp4; codecs="mp4a.40.2"',
            'audio/mp4': 'audio/mp4; codecs="mp4a.40.2"',
            'audio/x-m4a': 'audio/mp4; codecs="mp4a.40.2"'
        };

        const mimeType = mimeTypeMap[contentType.toLowerCase()] || contentType;

        // Check if MediaSource is supported and format is compatible
        const useMediaSource = typeof MediaSource !== 'undefined' &&
            MediaSource.isTypeSupported(mimeType) &&
            !isIOSSafari();

        if (useMediaSource) {
            console.log(`Using MediaSource API for progressive playback (${mimeType})`);
            return await playWithMediaSource(response, mimeType);
        } else {
            // iOS Safari or unsupported format - should not happen with proper endpoint selection
            console.warn(`MediaSource not available, this should use HLS endpoint instead`);
            throw new Error('MediaSource not supported. For iOS Safari, use stream_format=hls');
        }
    } else {
        // Non-streaming response - use blob directly
        console.log('Non-streaming response, using blob');
        const blob = await response.blob();
        const contentType = response.headers.get('content-type') || 'audio/aac';
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        currentAudioElement = audio;
        isPlaying = true;

        await new Promise((resolve, reject) => {
            audio.onended = () => {
                URL.revokeObjectURL(url);
                isPlaying = false;
                currentAudioElement = null;
                resolve();
            };
            audio.onerror = (e) => {
                console.error('Audio playback error:', e);
                URL.revokeObjectURL(url);
                isPlaying = false;
                currentAudioElement = null;
                reject(e);
            };
            audio.onpause = () => {
                if (shouldStop) {
                    URL.revokeObjectURL(url);
                    isPlaying = false;
                    currentAudioElement = null;
                    resolve();
                }
            };
            audio.play().catch(reject);
        });
    }
}

// Progressive HLS playback for iOS Safari
async function playWithHLS(playlistUrl) {
    console.log(`Using HLS for iOS Safari: ${playlistUrl}`);

    // Ensure absolute URL - iOS Safari requires full URL
    const baseURL = `${window.location.protocol}//${window.location.host}`;
    let fullPlaylistUrl = playlistUrl.startsWith('http') ? playlistUrl : `${baseURL}${playlistUrl}`;

    // Ensure the URL starts with the protocol
    if (!fullPlaylistUrl.startsWith('http://') && !fullPlaylistUrl.startsWith('https://')) {
        fullPlaylistUrl = `${window.location.protocol}//${window.location.host}${playlistUrl}`;
    }

    console.log(`Full HLS playlist URL: ${fullPlaylistUrl}`);

    // Wait for playlist to be ready with at least one segment
    // The server waits for segments, but we should verify the playlist is accessible
    let playlistReady = false;
    let attempts = 0;
    const maxAttempts = 40; // 4 seconds total (40 * 100ms)
    let lastError = null;

    while (!playlistReady && attempts < maxAttempts) {
        try {
            // Try fetching the actual playlist to see if it has segments
            const playlistResponse = await fetch(fullPlaylistUrl);
            if (playlistResponse.ok) {
                const playlistText = await playlistResponse.text();
                // Check if playlist has at least one segment (#EXTINF)
                if (playlistText.includes('#EXTINF')) {
                    playlistReady = true;
                    console.log('HLS playlist ready with segments');
                    break;
                } else {
                    console.log(`Playlist exists but no segments yet (attempt ${attempts + 1}/${maxAttempts})...`);
                }
            } else {
                lastError = `Playlist fetch returned ${playlistResponse.status}: ${playlistResponse.statusText}`;
                console.log(`Waiting for playlist (attempt ${attempts + 1}/${maxAttempts})... Status: ${playlistResponse.status}`);
            }
        } catch (e) {
            lastError = e.message;
            console.log(`Waiting for playlist (attempt ${attempts + 1}/${maxAttempts})... Error: ${e.message}`);
        }
        attempts++;
        await new Promise(resolve => setTimeout(resolve, 100));
    }

    if (!playlistReady) {
        throw new Error(`HLS playlist not ready - timed out waiting for segments. Last error: ${lastError || 'unknown'}`);
    }

    // Create audio element with HLS playlist
    const audio = new Audio();
    currentAudioElement = audio;
    isPlaying = true;

    try {
        // Set the source and load
        audio.src = fullPlaylistUrl;

        // Wait for canplay event before trying to play
        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                const errorMsg = audio.error
                    ? `Audio error code ${audio.error.code}: ${audio.error.message || 'Unknown error'}`
                    : 'Timeout waiting for audio to be ready';
                reject(new Error(errorMsg));
            }, 10000); // Increased timeout to 10 seconds

            const cleanup = () => {
                clearTimeout(timeout);
                audio.removeEventListener('canplay', onCanPlay);
                audio.removeEventListener('canplaythrough', onCanPlayThrough);
                audio.removeEventListener('error', onError);
                audio.removeEventListener('loadstart', onLoadStart);
                audio.removeEventListener('loadedmetadata', onLoadedMetadata);
            };

            const onCanPlay = () => {
                console.log('HLS: Can play');
                cleanup();
                resolve();
            };

            const onCanPlayThrough = () => {
                console.log('HLS: Can play through');
                cleanup();
                resolve();
            };

            const onError = (e) => {
                const errorMsg = audio.error
                    ? `Audio error code ${audio.error.code}: ${audio.error.message || 'Unknown error'}`
                    : 'Audio element error';
                console.error('HLS Audio error:', errorMsg, audio.error);
                cleanup();
                reject(new Error(errorMsg));
            };

            const onLoadStart = () => {
                console.log('HLS: Load started');
            };

            const onLoadedMetadata = () => {
                console.log('HLS: Metadata loaded');
            };

            // Add event listeners for debugging
            audio.addEventListener('loadstart', onLoadStart, { once: true });
            audio.addEventListener('loadedmetadata', onLoadedMetadata, { once: true });

            // These will resolve the promise
            audio.addEventListener('canplay', onCanPlay, { once: true });
            audio.addEventListener('canplaythrough', onCanPlayThrough, { once: true });
            audio.addEventListener('error', onError, { once: true });

            // Start loading
            audio.load();
        });

        await audio.play();
        console.log('HLS playback started');
    } catch (err) {
        console.error('Error starting HLS playback:', err);
        console.error('Playlist URL was:', fullPlaylistUrl);
        if (audio.error) {
            console.error('Audio error details:', {
                code: audio.error.code,
                message: audio.error.message
            });
        }
        stopCurrentPlayback();
        throw err;
    }

    // Wait for playback to complete
    await new Promise((resolve, reject) => {
        audio.onended = () => {
            console.log('HLS playback ended normally');
            isPlaying = false;
            currentAudioElement = null;
            resolve();
        };
        audio.onerror = (e) => {
            console.error('HLS playback error:', e);
            isPlaying = false;
            currentAudioElement = null;
            reject(e);
        };
        audio.onpause = () => {
            if (shouldStop) {
                isPlaying = false;
                currentAudioElement = null;
                resolve();
            }
        };
    });
}

// Progressive playback using MediaSource API
async function playWithMediaSource(response, mimeType) {
    const reader = response.body.getReader();
    streamReader = reader;

    // Create MediaSource
    const mediaSource = new MediaSource();
    currentMediaSource = mediaSource;
    const url = URL.createObjectURL(mediaSource);
    const audio = new Audio(url);
    currentAudioElement = audio;

    // Wait for MediaSource to be ready
    await new Promise((resolve) => {
        mediaSource.addEventListener('sourceopen', () => {
            try {
                // Create SourceBuffer
                const sourceBuffer = mediaSource.addSourceBuffer(mimeType);
                currentSourceBuffer = sourceBuffer;

                // Start reading chunks
                readAndAppendChunks(reader, sourceBuffer, mediaSource, audio, resolve);
            } catch (e) {
                console.error('Error creating SourceBuffer:', e);
                mediaSource.endOfStream();
                resolve();
            }
        }, { once: true });
    });

    // Start playback as soon as MediaSource is ready
    try {
        await audio.play();
        isPlaying = true;
        console.log('Progressive playback started');
    } catch (err) {
        console.error('Error starting playback:', err);
        stopCurrentPlayback();
        throw err;
    }

    // Wait for playback to complete
    await new Promise((resolve, reject) => {
        audio.onended = () => {
            console.log('Playback ended normally');
            cleanupMediaSource();
            resolve();
        };
        audio.onerror = (e) => {
            console.error('Audio playback error:', e);
            cleanupMediaSource();
            reject(e);
        };
        audio.onpause = () => {
            if (shouldStop) {
                cleanupMediaSource();
                resolve();
            }
        };
    });
}

// Read chunks and append to SourceBuffer progressively
async function readAndAppendChunks(reader, sourceBuffer, mediaSource, audio, onReady) {
    let firstChunk = true;
    let chunksQueued = 0;

    const appendChunk = async (chunk) => {
        if (shouldStop) {
            return;
        }

        // Wait if buffer is updating or full
        while (sourceBuffer.updating ||
            (sourceBuffer.buffered.length > 0 &&
                sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1) - audio.currentTime < 0.5)) {
            await new Promise(resolve => setTimeout(resolve, 10));
        }

        try {
            sourceBuffer.appendBuffer(chunk);
            chunksQueued++;

            if (firstChunk) {
                firstChunk = false;
                console.log('First chunk appended, playback can start');
                onReady();
            }
        } catch (e) {
            if (e.name !== 'QuotaExceededError') {
                console.error('Error appending chunk:', e);
            }
        }
    };

    try {
        while (true) {
            if (shouldStop) {
                reader.cancel();
                break;
            }

            const { done, value } = await reader.read();
            if (done) {
                // Wait for all chunks to be appended
                while (sourceBuffer.updating) {
                    await new Promise(resolve => setTimeout(resolve, 10));
                }
                console.log(`All chunks appended (${chunksQueued} chunks), ending stream`);
                mediaSource.endOfStream();
                break;
            }

            if (value && value.length > 0) {
                await appendChunk(value);
            }
        }
    } catch (e) {
        console.error('Error reading stream:', e);
        if (!shouldStop) {
            mediaSource.endOfStream('error');
        }
    }
}


// Cleanup MediaSource resources
function cleanupMediaSource() {
    if (currentMediaSource) {
        try {
            if (currentMediaSource.readyState === 'open') {
                currentMediaSource.endOfStream();
            }
            const url = currentAudioElement?.src;
            if (url) {
                URL.revokeObjectURL(url);
            }
        } catch (e) {
            console.warn('Error cleaning up MediaSource:', e);
        }
        currentMediaSource = null;
    }
    if (currentSourceBuffer) {
        currentSourceBuffer = null;
    }
    isPlaying = false;
    currentAudioElement = null;
}

function stopCurrentPlayback() {
    console.log('Stopping playback...');
    shouldStop = true;

    // Cancel stream reader if active
    if (streamReader) {
        try {
            streamReader.cancel();
            console.log('Stream reader cancelled');
        } catch (e) {
            console.warn('Error cancelling stream reader:', e);
        }
        streamReader = null;
    }

    // Pause and reset audio element
    if (currentAudioElement) {
        try {
            currentAudioElement.pause();
            currentAudioElement.currentTime = 0;
            console.log('Audio element paused');
        } catch (e) {
            console.warn('Error pausing audio:', e);
        }
    }

    // Cleanup MediaSource resources
    cleanupMediaSource();

    isPlaying = false;
}

// Get the current host and port
const baseURL = `${window.location.protocol}//${window.location.host}`;

// API key for requests (can be set via query parameter or use default)
const urlParams = new URLSearchParams(window.location.search);
const apiKey = urlParams.get('api_key') || 'your_api_key_here';

// We call the local `/v1/audio/speech` endpoint directly using fetch below.

const textInput = document.getElementById('textInput');
const voiceSelect = document.getElementById('voiceSelect');
const speedInput = document.getElementById('speedInput');
const formatSelect = document.getElementById('formatSelect');
const playButton = document.getElementById('playButton');
const stopButton = document.getElementById('stopButton');
const status = document.getElementById('status');

let currentPlaybackPromise = null;

function showStatus(message, type = 'info') {
    status.textContent = message;
    status.className = `status ${type} show`;
    setTimeout(() => {
        status.classList.remove('show');
    }, 5000);
}

function setLoading(loading) {
    playButton.disabled = loading;
    // Enable stop when there's an active playback promise or an active audio element
    stopButton.disabled = !(currentPlaybackPromise || currentAudioElement || isPlaying);
    if (loading) {
        playButton.textContent = '⏳ Generating...';
    } else {
        // If currently playing, show Pause, otherwise show Play
        if (currentAudioElement && !currentAudioElement.paused && isPlaying) {
            playButton.textContent = '⏸️ Pause';
        } else {
            playButton.textContent = '▶️ Play Audio';
        }
    }
}

function stopAudio() {
    stopCurrentPlayback();
    currentPlaybackPromise = null;
    setLoading(false);
    showStatus('Playback stopped', 'info');
}

playButton.addEventListener('click', async () => {
    // If there's an active audio element, toggle pause/resume
    if (currentAudioElement) {
        try {
            if (!currentAudioElement.paused) {
                currentAudioElement.pause();
                playButton.textContent = '▶️ Play Audio';
            } else {
                await currentAudioElement.play();
                playButton.textContent = '⏸️ Pause';
            }
        } catch (e) {
            console.warn('Play/pause toggle failed', e);
        }
        return;
    }

    const text = textInput.value.trim();
    if (!text) {
        showStatus('Please enter some text', 'error');
        return;
    }

    try {
        setLoading(true);
        showStatus('Generating audio...', 'info');

        if (currentAudioElement) {
            stopAudio();
        }

        const voice = voiceSelect.value;
        const responseFormat = formatSelect.value;
        const speed = parseFloat(speedInput.value);

        // iOS Safari doesn't support MediaSource well — use init/stream approach
        if (isIOSSafari()) {
            const initResponse = await fetch('/v1/audio/speech/init', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                body: JSON.stringify({ input: text, voice: voice, speed: speed, response_format: responseFormat })
            });

            if (!initResponse.ok) {
                const errText = await initResponse.text();
                throw new Error(errText || 'Failed to initialize audio stream');
            }

            const { stream_id, token } = await initResponse.json();

            const audio = new Audio();
            currentAudioElement = audio;
            isPlaying = true;
            // mark a non-null playback marker so stop button becomes enabled
            currentPlaybackPromise = {};
            audio.src = `/v1/audio/speech/stream/${stream_id}?token=${token}`;

            audio.addEventListener('canplay', () => {
                setLoading(false);
                showStatus('Playing...', 'info');
            }, { once: true });

            audio.addEventListener('ended', () => {
                setLoading(false);
                showStatus('Audio playback completed', 'success');
                currentAudioElement = null;
                isPlaying = false;
                currentPlaybackPromise = null;
            }, { once: true });

            audio.addEventListener('error', (e) => {
                console.error('Playback error', e);
                setLoading(false);
                showStatus('Playback error', 'error');
                currentAudioElement = null;
                isPlaying = false;
                currentPlaybackPromise = null;
            }, { once: true });

            await audio.play();
        } else {
            const response = await fetch('/v1/audio/speech', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                body: JSON.stringify({
                    input: text,
                    voice: voice,
                    response_format: responseFormat,
                    speed: speed
                })
            });

            if (!response.ok) {
                const errText = await response.text();
                throw new Error(errText || 'TTS request failed');
            }

            // Use existing progressive playback function that handles streaming
            currentPlaybackPromise = playAudioWithStop(response);
            try {
                await currentPlaybackPromise;
            } finally {
                currentPlaybackPromise = null;
            }
            setLoading(false);
            showStatus('Audio playback completed', 'success');
        }

    } catch (error) {
        console.error('Error:', error);
        showStatus(`Error: ${error.message}`, 'error');
        setLoading(false);
    }
});

stopButton.addEventListener('click', () => {
    stopAudio();
});

// Show initial status
showStatus('Ready to test TTS. Enter text and click Play.', 'info');

