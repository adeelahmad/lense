# Components

Lens fetches the engines and models it needs. After the install line and a model provider set in the web app, there is
nothing to install by hand.

Each worker looks after its own machine. It compares what the settings ask for with what's installed, and fetches
what's missing in the background, one thing at a time. Engines and models for job steps are fetched **on first use**:
the first time an asset is waiting for the step that needs them. Until then they show as *On first use*, and an
archive that only uses cloud models never downloads them, which keeps a small machine (a Raspberry Pi) small. Documents,
images and transcripts imported without their audio (pasted ones too) skip the transcribe and diarize steps, so they
never fetch a speech or voice model.

| Component | Fetched when | What |
|---|---|---|
| PyTorch | SenseVoice or voice IDs are used | the CPU build when the machine has no NVIDIA GPU (about 900 MB instead of several GB) |
| SenseVoice | `transcribe.engine` is `sensevoice` (the default) | FunASR and the SenseVoice and voice-activity models |
| Whisper model | `transcribe.engine` is `whisper` | the faster-whisper model named in `transcribe.whisper.model` |
| Voice IDs | speakers are separated by clustering (the default) | SpeechBrain and its ECAPA voiceprint model |
| Face models | `video.face_engine` is `opencv` (the default) | OpenCV, YuNet and SFace |
| Face models | `video.face_engine` is `anytopdf` | SFace, for anytopdf to describe faces with (anytopdf too) |
| Object model | `video.object_engine` is `yolox` (the default) or `anytopdf` | YOLOX-s, and ONNX Runtime for `yolox` |
| NSFW model | [NSFW classification](nsfw.md) is on for the archive or a namespace (off by default) | the 8-bit ONNX export of AdamCodd/vit-base-nsfw-detector (Apache-2.0, 88 MB), and ONNX Runtime |
| Prompt-injection classifier | `ai.injection_check` is `classifier` | ProtectAI's deberta-v3-base-prompt-injection-v2 (Apache-2.0, about 740 MB), the tokenizers library and ONNX Runtime ([third-party content](assistant.md#third-party-content-and-prompt-injection)) |
| Punctuation model | `analysis.cleanup` is on and `analysis.cleanup_punctuation` isn't `off` ([Cleaning up transcripts](processing.md#cleaning-up-transcripts)) | kredor/punctuate-all at a pinned revision (MIT, about 1.1 GB), with Transformers and PyTorch's CPU build |
| spaCy | `analysis.entities` is `spacy` ([Entities](processing.md#entities-and-the-graph-explorer)) | spaCy (MIT) and the model in `analysis.spacy_model`: `en_core_web_sm`, `de_core_news_sm` or `xx_ent_wiki_sm` (MIT, 11 to 15 MB each), about 60 MB in all |
| DuckDuckGo search | `web.search` is `duckduckgo` | ddgs (MIT, about 30 MB with lxml and primp), the client the assistant's [web search](assistant.md#web-search-and-reading-pages) reads DuckDuckGo's results with |
| yt-dlp | `documents.video_urls` is on | the yt-dlp program, release 2026.08.19 (Unlicense), to download videos and podcasts imported by address |
| Chat model, embedding model | the LLM provider or embeddings server is Ollama | pulled on that server (`/api/pull`) |
| Code sandbox client | the [code sandbox](assistant.md#code-sandbox) is on (`sandbox.enabled`) | OpenShell's Python SDK (Apache-2.0) and gRPC, about 10 MB, to reach its gateway |
| Outlook .msg emails | listed in `components.also` | extract-msg (GPL-3.0, so only when asked for) |

- Python packages go into `data_dir/python`, at the versions in `uv.lock`. Packages the image already has win.
- Models go into `data_dir/models`: the Hugging Face, ModelScope and PyTorch caches point there (unless `HF_HOME`,
  `MODELSCOPE_CACHE` or `TORCH_HOME` say otherwise). They outlast container rebuilds and upgrades.
- Model files fetched by URL (YOLOX, YuNet, SFace, the NSFW model, spaCy's models) are checked against their SHA-256.
- A job step that needs something still being fetched waits for it instead of falling back or failing. If a fetch fails,
  the step runs as before (for transcription, falling back to another engine), and the fetch is tried again an hour
  later, when the settings change, or on **Check again now**.

Programs are only checked: a running container can't install them. ffmpeg and Tesseract are in every image. Chromium and
LibreOffice (web pages, text, emails, Office files) are in the full image, which the compose files build by default. In
a [microsandbox](microsandbox.md#libreoffice-and-chromium)'s full edition they're installed the first time a document or
a web page needs one, and the Components list says so.

## Settings

| Setting | Default | |
|---|---|---|
| `components.auto` | `true` | fetch what's needed without asking; off, the workers only report what's missing |
| `components.ahead` | `false` | fetch everything the settings could need straight away, instead of on first use |
| `components.also` | `[]` | optional components to fetch too, e.g. `["msg"]` |
| `components.hf_token` | none | a Hugging Face token, kept encrypted, for gated models: pyannote's speaker diarization, and GGUF models whose page asks you to accept terms. `HF_TOKEN` in the environment wins over it |

**Settings → Components** shows each worker's machine (processors, memory, GPU, free disk), each component and where
every worker is with it, and the transcription engine that suits the machine. `GET /api/v1/components` returns the
same; `POST /api/v1/components/check` asks the workers to look again now.

The setup assistant's `server_status` lists what the workers are still fetching or couldn't fetch.
