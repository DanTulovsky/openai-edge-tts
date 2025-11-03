# HLS Implementation (REMOVED)

The HLS implementation has been removed from this repository. If you previously relied on HLS for iOS Safari compatibility, migrate to the progressive MP3 approach:

- Call the local OpenAI-compatible endpoint at `/v1/audio/speech` with `response_format: 'mp3'` and play the returned blob via the `Audio` element.
- Example client-side usage (already included in `app/static/js/tts-test.js`):

```javascript
const response = await fetch('/v1/audio/speech', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ input: text, response_format: 'mp3', voice })
});
const blob = await response.blob();
const audio = new Audio(URL.createObjectURL(blob));
await audio.play();
```

For lower-latency or future work, consider adding an AudioWorklet-based player (iOS 17.1+ and modern browsers). The repository no longer maintains HLS-specific code or tests.

