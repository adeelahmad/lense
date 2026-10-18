- **Clean up a recording's sound before it's transcribed.** A new pipeline step, Clean up audio (`clean_audio`), runs a
  recording through WebRTC's audio processing (pywebrtc-audio, Apache-2.0, about 0.5 MB, fetched the first time it
  runs): noise suppression, automatic gain, a high-pass filter, echo cancellation for two-channel call recordings, and
  pauses left out of what's transcribed, with the transcript's times put back on the recording's timeline. Settings →
  Transcription → Audio cleanup holds what it does; no pipeline has it until you add it, the file is never changed, and
  `GET /api/v1/recordings/{id}` says what it found (`audio_cleanup`)
  ([Cleaning up audio](docs/processing.md#cleaning-up-audio)).
