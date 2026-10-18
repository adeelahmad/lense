- **Clean up only low quality recordings.** The `clean_audio` step can now measure each recording's signal-to-noise
  and clipping first and leave a clean-sounding one as it is, cleaning only the noisy, quiet or clipped ones. Off by
  default (every recording is still cleaned); turn on "Clean only low quality recordings" under Settings →
  Transcription → Audio cleanup and set the signal-to-noise floor. What it measured (`snr_db`, `clip_pct`) is on the
  recording in `GET /api/v1/recordings/{id}` ([Cleaning up audio](docs/processing.md#cleaning-up-audio)).
