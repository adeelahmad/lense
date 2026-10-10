# Speech providers

Lens transcribes, tells speakers apart and reads emotion on your own machine by default (SenseVoice, Whisper,
SpeechBrain). Nothing changes until an admin picks a provider. Any of these voice tasks can go to a speech provider
instead, which helps on a small machine such as a Raspberry Pi, or when you want a provider's accuracy.

Status: built (transcription, speakers, language, sentiment, audio events, text to speech). Planned: provider
summaries, chapters and entities as optional imports, per-minute prices in the activity ledger.

## Providers

| Provider | Speech to text | Speakers | Language | Emotion | Sounds | Text to speech |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAI-compatible | `/audio/transcriptions` (whisper-1, gpt-4o-transcribe) | models named `…diarize` | yes | no | no | `/audio/speech` |
| ElevenLabs | Scribe | yes | yes | no | laughter, applause, … | yes |
| AssemblyAI | Universal, Slam-1 | yes | detected | from sentiment | no | no |
| Deepgram | Nova | yes | detected | from sentiment (English) | no | Aura |
| Google Gemini | no | no | no | no | no | `gemini-2.5-flash-preview-tts`, 30 prebuilt voices |
| macOS `say` | no | no | no | no | no | the Mac's own voices; no key, nothing leaves the Mac |

Sentiment becomes emotion as positive → Happy, negative → Sad, neutral → Neutral (turn it off with
`speech.sentiment`). SenseVoice's own emotion tags stay richer; a provider only reports sentiment.

## Settings

**Settings → Speech providers** has, for each provider, an address, a speech-to-text model and an API key. Keys are
stored encrypted like every other key. The Test button checks the address and key without billing anything.

Every address can be changed: point it at a proxy, a regional endpoint (`https://api.eu.assemblyai.com`), a
self-hosted Deepgram, or any server that speaks the OpenAI audio API (speaches, LocalAI, Groq's
`https://api.groq.com/openai/v1`). An OpenAI-compatible server on your own network needs no key.

Then pick where each task goes:

- **Transcription → Engine**: SenseVoice, Whisper, mlx-whisper, or a provider. A provider's transcript carries its
  speakers, language, sounds and sentiment.
- **Speaker separation → Method**: *Speech provider* uses the speakers the provider found; *Auto* uses them when a
  transcript has some, and otherwise works as before. Voice IDs across recordings still come from local voiceprints.
- **AI assistant → Voice**: *Engine that hears it* (default: the transcription engine) and *Spoken answers by* (an
  OpenAI-compatible speech server, ElevenLabs, Deepgram Aura, Google Gemini or macOS `say`) and *Voice*.

## Voices

`GET /voice/voices?provider=…` lists the voices a provider reads in, as `{id, name, detail}`: ElevenLabs lists the
voices in your account (premade, cloned and from the library), Deepgram its Aura models, `say` the voices installed on
the Mac (System Settings → Accessibility → Spoken Content adds more), and an OpenAI-compatible server its own when it
has `/audio/voices` (Kokoro-FastAPI does); otherwise OpenAI's eleven. Gemini's are a fixed list. The voice picked is
`voice.tts_voice`; podcast hosts use it too (`podcasts.voice_a`, `voice_b`). `POST /voice/speak` takes `{text,
voice}` to try a voice before keeping it.

`say` only works when Lens runs on the Mac itself (`make start-backend`, not Docker): a container has no `say`. It
writes WAV, and both podcast hosts get the same voice unless `podcasts.voice_b` names another, since no voice is on
every Mac.

| Setting | Default |
| --- | --- |
| `speech.openai_base_url`, `openai_model` | `https://api.openai.com/v1`, `whisper-1` |
| `speech.elevenlabs_base_url`, `elevenlabs_model` | `https://api.elevenlabs.io`, `scribe_v1` |
| `speech.assemblyai_base_url`, `assemblyai_model` | `https://api.assemblyai.com`, `universal` |
| `speech.deepgram_base_url`, `deepgram_model` | `https://api.deepgram.com`, `nova-3` |
| `speech.gemini_base_url`, `gemini_model` | `https://generativelanguage.googleapis.com`, `gemini-2.5-flash-preview-tts` |
| `speech.sentiment` | on |
| `speech.timeout` | 1800 seconds |
| `voice.stt` | `same` |
| `voice.tts_provider` | `openai` |
| `voice.tts_voice` | the provider's default (alloy, a premade ElevenLabs voice, aura-2-thalia-en, Kore, the Mac's) |

## What is sent

Only the audio of the recording (or the clip said into the mic), encoded as Ogg Opus at 32 kbit/s (FLAC when ffmpeg
has no Opus). Recordings longer than ten minutes go to OpenAI-compatible servers in ten-minute parts, since they take
at most 25 MB a request; speaker labels then belong to each part. Text to speech sends the answer's text.

Code: `fastapi_backend/app/domain/speech.py`.
