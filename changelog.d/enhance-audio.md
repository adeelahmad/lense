- **Clean up low quality sound before it's transcribed.** The `enhance` step now listens to audio and video too:
  a noisy, quiet or clipped recording gets a cleaned-up copy (declipped, rumble and steady noise taken out, its level
  evened) that transcription hears, in step with the original sample for sample, so words land where they were said;
  the original, its waveform and voice matching stay as they were. Off by default: Settings → Enhance. ffmpeg's
  filters are the default; WebRTC's noise suppression and gain (webrtc-noise-gain, Apache-2.0) is fetched the first
  time it's chosen. Enhance and transcribe again from a recording's Details tab, the API, MCP or an automation
  ([Enhancing images and sound](docs/enhance.md)).
