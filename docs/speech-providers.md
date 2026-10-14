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
| StyleTTS2 | no | no | no | no | no | on this machine, in a voice like a clip you give it ([below](#styletts2)) |

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

## Long recordings

Each provider takes so much audio in one request. A recording longer or larger than that is cut into pieces, each
piece is transcribed on its own, and the transcripts are joined back into one, timed from the start of the recording.
Nothing needs setting up: every provider starts with a limit from its published one.

| Provider | Longest piece | Largest file | Where the default comes from |
| --- | --- | --- | --- |
| OpenAI-compatible | 10 minutes | 25 MB | [OpenAI](https://platform.openai.com/docs/guides/speech-to-text): 25 MB a file; GPT-4o transcription takes up to 25 minutes, and shorter pieces suit servers with smaller limits |
| ElevenLabs | 600 minutes | 3000 MB | [ElevenLabs](https://elevenlabs.io/docs/capabilities/speech-to-text): up to 10 hours and 3 GB |
| AssemblyAI | 600 minutes | 2200 MB | [AssemblyAI](https://www.assemblyai.com/docs/faq/are-there-any-limits-on-file-size-or-file-duration-for-files-submitted-to-the-api): up to 10 hours; 2.2 GB through its upload endpoint |
| Deepgram | 120 minutes | 2000 MB | [Deepgram](https://developers.deepgram.com/docs/pre-recorded-audio): 2 GB; it stops a request after 10 minutes of work, so two hours at a time |

Change them in **Settings → Speech providers** (*Longest piece sent at once*, *Largest file sent at once*), or as
`speech.{provider}_max_minutes` and `speech.{provider}_max_mb` through the settings API or the assistant's
`change_settings` tool: lower them for a proxy or a compatible server that takes less (Groq's free tier, a small
self-hosted server), raise them for one that takes more.

How a recording is cut:

- A piece ends at the quietest tenth of a second in the half minute before the limit, so a cut falls in a pause rather
  in the middle of a word. Pieces meet exactly: nothing is sent twice or left out.
- Each piece is encoded to send; one whose file is still larger than the limit is halved, again at a pause, until each
  part fits.
- The provider numbers speakers afresh in every request, so a speaker label belongs to its piece (`c0:S1`, `c1:S1`).
  Voice IDs still come from local voiceprints and match voices already known; a new voice heard in two pieces shows as
  two speakers until you merge them.
- The activity ledger keeps one row for the recording, with its length in seconds and the number of pieces.

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
| `voice.tts_voice` | the provider's default (alloy, a premade ElevenLabs voice, aura-2-thalia-en, Kore, the Mac's, `default`) |
| `voice.styletts2_url` | `http://styletts2:8880` |

## StyleTTS2

[StyleTTS2](https://github.com/yl4579/StyleTTS2) reads aloud on your own machine: nothing leaves it, and nothing is
billed. It is heavy (PyTorch, a 2 GB image and a 1 GB model fetched from Hugging Face on first use; a sentence takes
a few seconds on a recent CPU), so it is off by default and runs as its own service, `styletts2/`:

```sh
docker compose --profile styletts2 up -d
```

Then pick **StyleTTS2** in Settings → AI assistant → Voice. `voice.styletts2_url` is where Lens reaches it
(`http://styletts2:8880`, the Compose service). It speaks the OpenAI audio API (`POST /v1/audio/speech`,
`GET /v1/audio/voices`).

Its voices are clips: put a few seconds of someone speaking (`.wav`, `.mp3` or `.flac`, named after the voice, e.g.
`grandpa.wav`) in the `styletts2-voices` volume and it reads in a voice like theirs. `default` is a public-domain
LibriVox reader. The model's authors ask that listeners are told the speech is synthesized, unless you have
permission to use the voice.

## What is sent

Only the audio of the recording (or the clip said into the mic), encoded as Ogg Opus at 32 kbit/s (FLAC when ffmpeg
has no Opus), in pieces when it is longer or larger than the provider takes at once ([above](#long-recordings)). Text
to speech sends the answer's text.

Code: `fastapi_backend/app/domain/speech.py`.
