# Voice service

The **Voice** workspace in Lens turns raw text into downloadable audio and an audio or video clip into text.
It uses the same speech providers as the assistant and podcasts. There is no second service to install, no new
queue, and no model download until an administrator selects a local engine. Open **Voice** beside **Chat**, or use
the link in **Settings → AI assistant → Voice**.

## Text and media

- Paste up to 4,000 characters, choose one of the configured provider's voices, and generate audio. The text is sent
  exactly as entered, including punctuation and Markdown. The workspace plays and downloads the returned audio.
  A missing provider or failed synthesis is an error, not a silent browser-speech substitute or truncated recording.
- Upload an audio or video clip, up to 25 MiB and five minutes, to get a transcript you can copy or download. This
  does not create a Library asset. Use normal Library import for longer recordings and background transcription.
- Cancel stops the browser waiting and discards late results. A model call already running on the server can finish;
  cancellation does not promise to stop provider billing.

Readiness, transcription engine, and provider voice-list errors are shown in the workspace. Configure the engines in
**Settings → AI assistant → Voice** and keys in **Settings → Speech providers**. See [speech providers](speech-providers.md)
for real capability and platform requirements. Kokoro and StyleTTS2 are supported existing integrations. Piper is
not a native Lens backend: an independently operated server that actually implements the OpenAI audio API can be
configured as OpenAI-compatible, but Lens does not install or validate a Piper deployment here. macOS `say` cannot
run in a Linux Proxmox container.

## The same API locally and remotely

Use your existing Lens origin, including its existing HTTPS/Cloudflare Tunnel address, with `/api/v1`:

```sh
# An ordinary Lens API token, kept in the shell environment, not in the URL.
curl --fail-with-body "$LENS_URL/api/v1/voice/speak" \
  -H "Authorization: Bearer $LENS_TOKEN" -H 'Content-Type: application/json' \
  --data '{"text":"Read this exact text.","server_only":true}' --output speech.audio

curl --fail-with-body "$LENS_URL/api/v1/voice/transcribe" \
  -H "Authorization: Bearer $LENS_TOKEN" -H 'Content-Type: application/octet-stream' \
  --data-binary @clip.wav
```

The speech response's `Content-Type` identifies its format; do not assume every provider returns MP3.
Transcription returns `text`, `language`, `engine` and `seconds`. `GET /voice` reports capability, and authenticated
`GET /voice/voices` lists selectable voices. A namespace-only key deliberately cannot use these global endpoints.
The workspace asks for `server_only:true`. Existing assistant clients can still omit it and receive 204 for browser
fallback. Blank or over-limit text returns 422; malformed media 400, oversized/long media 413, unavailable speech 503,
and an unconfigured transcription engine 409. Busy/rate-limited requests return 429 with `Retry-After`.

This feature does not create a tunnel, change firewall rules, install models on a live host, or upgrade an existing
v0.20.0 instance. Those remain operator actions after the change is reviewed and released.

## Anonymous access, only when needed

Authentication stays required by default. An administrator can enable **Allow anonymous raw voice API** in the
Voice settings (`voice.public_enabled`) and switch it off again afterward. This opens only capability, raw speech
and raw transcription. It does not open the Voice web page, provider voice lists, settings, the archive, podcasts,
or private feeds. Anonymous callers omit the Authorization header and cannot override the configured voice.
Invalid or namespace-scoped credentials do not downgrade to anonymous access.

Enabling this on an internet-reachable instance lets anyone who can reach it consume its configured speech service,
including paid providers. The raw service has no waiting queue: at most two expensive requests run per API process,
and all anonymous callers share ten provider requests per minute per process. A worker keeps its slot until it
finishes, even when the HTTP request is cancelled. At most two media uploads are admitted concurrently.
Forwarding headers do not create a fresh quota.
Uploads time out after 30 seconds; decoding is bounded to 301 seconds of output, rejects anything over 300 seconds,
and has a 30-second wall-clock timeout. Decoding accepts only media formats and local file/pipe protocols, not URL
playlists. Successful audio and transcript responses use `Cache-Control: no-store`.

These are process-local safeguards, not a global internet abuse firewall. Multiple API workers multiply the limits.
Keep anonymous mode off when not in use, configure global budgets and provider spending limits, and use an edge
rate limit for a publicly exposed deployment. Raw requests are not archive assets, but the activity ledger still
records speech use; a configured cloud provider receives the supplied text/audio under its own retention policy.

## Sources, podcasts and Apple Podcasts

Import a web URL through Lens's guarded web importer, paste text through text import, or upload a document through
the Library. Wait for its text to be extracted, then select the asset and choose **Podcast**. This preserves the
existing URL network guard, source citations, namespace/vault permissions and podcast jobs rather than making an
anonymous URL-fetching service. Do not send URLs to the raw transcription endpoint: it accepts media bytes.

Episodes use Lens's existing [podcast flow](podcasts.md). Follow a saved collection or a published private podcast
[feed](feeds.md) in Apple Podcasts using **Follow a Show by URL**. An episode must have audio and be in that feed's
selection. A script without a working TTS provider is not an audio episode. Feed URLs are bearer secrets: anyone who
has the URL can read that feed until its token is rotated. Do not publish a private feed to a podcast directory.

## Alexa

Alexa playback is not implemented by this change. A realistic follow-up is a private Alexa skill using Amazon's
AudioPlayer interface and an HTTPS audio URL reachable by Amazon, with suitable authentication/linking and verified
audio requirements. The Lens API must not be treated as an Alexa Skill endpoint, and a private Apple Podcasts feed
does not automatically become available on Alexa. Building or publishing such a skill, and exposing its media,
requires separate configuration and verification.
