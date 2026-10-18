"""Configuration, the SurrealDB store, labels and small shared helpers.

The archive runs against SurrealDB in one of two ways, chosen by database.url (or $SURREAL_URL):
  surrealkv://path or mem://   embedded in this process, no server needed (one process at a time)
  ws://host:8000               a SurrealDB server, shared by the web app, batch jobs and other machines
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import os
import pathlib
import logging
import queue
import random
import re
import threading
import time

EMOTIONS = [
    "Neutral",
    "Happy",
    "Joy",
    "Amusement",
    "Relief",
    "Surprise",
    "Anxiety",
    "Guilt",
    "Sad",
    "Angry",
    "Fear",
    "Disgust",
    "Shame",
    "Love",
]
PALETTE = {
    "Neutral": "#6c757d",
    "Happy": "#ffc107",
    "Sad": "#007bff",
    "Angry": "#dc3545",
    "Fear": "#6f42c1",
    "Disgust": "#e83e8c",
    "Surprise": "#17a2b8",
    "Amusement": "#fd7e14",
    "Joy": "#28a745",
    "Love": "#d63384",
    "Relief": "#20c997",
    "Anxiety": "#842029",
    "Guilt": "#495057",
    "Shame": "#343a40",
}
EMOJI = {
    "Neutral": "😐",
    "Happy": "😄",
    "Joy": "🤩",
    "Amusement": "😆",
    "Relief": "😌",
    "Anxiety": "😰",
    "Guilt": "😔",
    "Surprise": "😲",
    "Sad": "😢",
    "Angry": "😠",
    "Fear": "😨",
    "Disgust": "🤢",
    "Shame": "😳",
    "Love": "🥰",
}
SV_EMOTION = {
    "HAPPY": "Happy",
    "SAD": "Sad",
    "ANGRY": "Angry",
    "NEUTRAL": "Neutral",
    "FEARFUL": "Fear",
    "DISGUSTED": "Disgust",
    "SURPRISED": "Surprise",
}
SV_EVENTS = {"Speech", "BGM", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough"}
EVENT_EMOJI = {"Laughter": "😂", "Applause": "👏", "BGM": "🎵", "Cry": "😭", "Cough": "🤧", "Sneeze": "🤧"}
ALIASES = {
    "Fearful": "Fear",
    "Surprised": "Surprise",
    "Disgusted": "Disgust",
    "Sadness": "Sad",
    "Anger": "Angry",
    "Happiness": "Happy",
    "N": "Neutral",
}
SPEAKER_COLORS = ["#2F6690", "#C2571A", "#5B7F2B", "#7A4E9A", "#A23B5B", "#2E8A82", "#8C6D1F", "#4F5D75"]
API = "/api/v1"  # where the HTTP API lives; media links built here are signed by the API layer before they leave
SEG = 1_000_000  # segment id = recording id * SEG + index, so ids are stable and need no lookups


def norm_emotion(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in EMOTIONS:
        return s
    if s.upper() in SV_EMOTION:
        return SV_EMOTION[s.upper()]
    if s in ALIASES:
        return ALIASES[s]
    c = s.capitalize()
    return c if c in EMOTIONS else ALIASES.get(c)


def norm_event(v):
    """A sound as segments hold it (Laughter, BGM …), however it's typed; music is BGM. Unknown ones are kept as given."""
    s = str(v or "").strip()
    if not s:
        return None
    known = {e.casefold(): e for e in SV_EVENTS} | {"music": "BGM"}
    return known.get(s.casefold(), s)


def labels():
    return {"palette": PALETTE, "emoji": EMOJI, "events": EVENT_EMOJI, "speakers": SPEAKER_COLORS}


# Audio and video the folder scans import, and the types uploads accept unless changed.
MEDIA_EXT = (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".webm", ".amr", ".mov", ".mkv", ".m4v", ".avi")
# documents and images, which uploads also accept (domain/documents.py): PDFs, and files made into PDFs to read
# (domain/convert.py): Office and OpenDocument files, text and Markdown, saved web pages, and emails
OFFICE_EXT = (".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx", ".odp", ".xls", ".xlsx", ".ods")
TEXT_EXT = (".txt", ".text", ".md", ".markdown", ".mdx")
PAGE_EXT = (".html", ".htm")
EMAIL_EXT = (".eml", ".msg")
ARCHIVE_EXT = (".zip", ".tar", ".tgz")  # a contents page, and the files in it kept (domain/archives.py)
DOCUMENT_EXT = (".pdf", *OFFICE_EXT, *TEXT_EXT, *PAGE_EXT, *EMAIL_EXT, *ARCHIVE_EXT)
STRUCTURED_EXT = (".json", ".jsonl")  # read as records, a chat or a transcript (domain/records.py)
RAW_EXT = (".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2")  # camera RAW
# photos Pillow can't open: anytopdf reads them (RAW from its embedded preview; HEIC where libheif is beside it)
CAMERA_EXT = (*RAW_EXT, ".heic", ".heif")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp", *CAMERA_EXT)
DEFAULTS = {
    "data_dir": "./archive-data",
    "database": {"url": None, "namespace": "archive", "database": "main", "user": "root", "password": "root"},
    "namespaces": {},
    "audio": {"extensions": list(MEDIA_EXT), "path_map": {}},
    "transcribe": {
        "engine": "sensevoice",
        "device": "auto",
        "language": "auto",
        "sensevoice": {"model": "iic/SenseVoiceSmall", "hub": "ms", "vad_max_segment_ms": 30000, "batch_size": 16},
        "whisper": {"model": "large-v3-turbo", "compute_type": "default"},
        "mlx_whisper": {"model": "mlx-community/whisper-large-v3-turbo"},
        # what the clean_audio step does to a recording's sound before it's transcribed (audio_cleanup.py); a pipeline only
        # runs it when the step is in it
        "cleanup": {
            "noise": "moderate",
            "gain": True,
            "high_pass": True,
            "echo": "off",
            "trim_silence": True,
            "min_silence_ms": 2000,
            "only_if_poor": False,  # clean every recording the step runs on; True cleans only ones measured low quality
            "snr_floor_db": 20,  # with only_if_poor, a recording at or above this signal-to-noise is left as it is
        },
        "vocabulary": False,  # give each recording's engine the names its namespace knows (vocabulary.py)
    },
    "diarize": {
        "engine": "auto",
        "cluster_threshold": 0.55,
        "min_speakers": None,
        "max_speakers": None,
        "pyannote": {"model": "pyannote/speaker-diarization-3.1", "token_env": "HF_TOKEN"},
    },
    "speakers": {
        "embedder": "speechbrain",
        "model": "speechbrain/spkrec-ecapa-voxceleb",
        "match_threshold": 0.5,
        "review_threshold": 0.35,
        "sample_seconds": 60,
        "cross_namespace": "suggest",
    },
    # anytopdf: the places and dates anytopdf (run here) finds, as hints for the extractor (enrich.py); dates: the dates
    # each line gives kept as days on the calendar (dates.py); cleanup: the
    # clean step tidies transcripts before analysis (cleanup.py), cleanup_fillers taking out "um" and "uh" too, and
    # cleanup_punctuation (off, missing, always) putting marks back with a local model, cleanup_grammar_url a
    # LanguageTool server's grammar and punctuation fixes (the compose files' `languagetool` profile); typing: which
    # model types the names the rules leave terms (name_types.asking)
    "analysis": {
        "entities": "rules",
        "typing": "auto",
        "spacy_model": "en_core_web_sm",
        "gazetteer": [],
        "anytopdf": False,
        "dates": False,
        "cleanup": False,
        "cleanup_fillers": True,
        "cleanup_punctuation": "off",
        "cleanup_grammar_url": None,
    },
    # chat_models: the models people may pick in Chat; empty: whatever the model server lists (docs/configuration.md)
    "llm": {
        "base_url": None,
        "model": None,
        "api_key_env": None,
        "max_chars": 24000,
        "timeout": 300,
        "chat_models": [],
        # a model the admin knows can see images: it describes pages and shots (the describe step); none, none are
        "vision_model": None,
        "describe_max": 50,  # pages or shots of a resource described at most
        # what describes them: "llm" (vision_model, above), or on this machine "florence-2" (florence.py) or
        # "moondream" (moondream.py)
        "describe_engine": "llm",
        "florence_model": "base",  # Florence-2 base (about 280 MB) or large (about 830 MB)
        "moondream_model": "moondream3-preview",  # Moondream 3 Preview (BSL 1.1, a GPU) or moondream2 (Apache-2.0)
    },
    "graph": {"max_nodes": 150, "min_edge_weight": 2},
    "search": {
        "stemming": "english",
        "engine": "sqlite",
        "opensearch_url": "",
        "opensearch_user": "",
        "opensearch_password": None,
        "opensearch_verify": True,
    },
    # search by meaning (app/domain/semantic.py): an OpenAI-compatible embeddings server (null: the LLM provider's),
    # the model, how long passages are, and how alike a passage must be to a query (null: what suits the model)
    "embeddings": {
        "enabled": True,
        "base_url": None,
        "model": "nomic-embed-text",
        "api_key_env": None,
        "query_prefix": None,
        "document_prefix": None,
        "passage_chars": 800,
        "batch_size": 32,
        "neighbours": 40,
        "min_similarity": None,
        "timeout": 60,
        "photos": False,  # search photos by what they show, with anytopdf's CLIP plugin (photos.py; a 600 MB model)
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8770,
        "allowed_hosts": ["127.0.0.1", "localhost"],
        "embed_frame_ancestors": ["'self'"],
        "max_upload_mb": 50,
        "session_hours": 168,
        "secure_cookies": False,
        # proxies whose X-Forwarded-For names the visitor's address, for IP groups (docs/configuration.md)
        "trusted_proxies": ["127.0.0.0/8", "::1/128"],
    },
    # signing in (docs/access.md#signing-in): whether passwords work at all. Passkeys always do. Fresh installs start
    # without passwords; an install that already had them keeps them until an admin turns them off.
    "auth": {"passwords": False},
    # how long API keys last (docs/configuration.md): what a new key gets, the most it may get, and whether keys may
    # never expire; and whether apps and MCP clients may sign people in through OAuth (domain/oauth.py), and how long
    # their tokens last: the access token, and the grant after the app last renewed it
    "tokens": {
        "default_days": 90,
        "max_days": 365,
        "never_expire": False,
        "oauth_enabled": True,
        "oauth_access_minutes": 60,
        "oauth_refresh_days": 30,
    },
    # audio, video, documents and images uploaded in the web app, in pieces (docs/configuration.md); transcript files use
    # server.max_upload_mb
    # where Lens keeps the files it makes its own, such as notes' attachments (blobs.py, docs/storage.md): "local"
    # (data_dir/objects) or "connection" (a storage source, under its folder); crypt wraps it in rclone crypt too
    "files": {"store": "local", "connection": None, "folder": "lens", "crypt": False},
    "uploads": {
        "max_mb": 4096,
        "extensions": list(MEDIA_EXT + DOCUMENT_EXT + IMAGE_EXT + STRUCTURED_EXT),
        "chunk_mb": 8,
        "expire_hours": 24,
    },
    # documents and images (docs/configuration.md): how large their pages are drawn, when a page is read by OCR, and
    # how many pages are read at most
    "documents": {
        "page_pixels": 2000,
        "thumb_pixels": 360,
        "ocr_below_chars": 25,
        "max_pages": 2000,
        "convert_seconds": 300,
        "attachment_resources": True,
        "soffice": None,
        "chromium": None,
        "web_networks": [],
        "video_urls": False,  # mediaurl.py: videos and podcasts imported by their address, downloaded by yt-dlp
        "video_captions": False,  # and their own captions, written by people, used as the transcript instead of transcribing
        "converter": "auto",  # anytopdf.py: auto (where Lens can't convert), anytopdf (every document and image), lens
        "anytopdf": None,  # the anytopdf program; None: the one downloaded on first use, else one on PATH
        "anytopdf_url": None,  # a conversion node: `anytopdf queue serve` on another machine
        "anytopdf_token": None,
        "tika_url": None,  # tika.py: a Tika server (the compose files' `tika` profile: http://tika:9998); None: off
        "tika_text": True,  # with a Tika server: it reads, as text, documents Lens can't make into PDFs
        "tika_metadata": True,  # with a Tika server: it reads what every document says about itself
    },
    "workers": {
        "inline": 1,
        "poll_seconds": 2,
        "stale_minutes": 15,
        "max_attempts": 3,
        # quiet processing (quiet.py): a window like "01:00-06:00" for the heavy steps, and how many run at once; off
        "heavy_hours": None,
        "heavy_at_once": None,
        "heavy_steps": ["transcribe", "read", "diarize", "shots", "ocr", "faces", "objects", "nsfw"],
        "timezone": "UTC",
        "steps": [
            "enhance",
            "clean_audio",
            "transcribe",
            "read",
            "diarize",
            "shots",
            "ocr",
            "faces",
            "objects",
            "nsfw",
            "describe",
            "convert",
            "clean",
            "analyze",
            "embed",
            "summarize",
            "llm",
            "report",
            "export",
            "workflow",
            "podcast",
        ],
    },
    # video: sampling, shot detection, OCR and faces. Model paths are bootstrap-only (the app can't point at arbitrary files).
    # the chat assistant's tools, and the double check before batch runs
    "ai": {
        "tools": True,
        "extensions": True,  # tools, skills, hooks and plugins people add (extensions.py)
        "refine_notes": True,  # the model keeps notes' titles and one-line summaries true to the whole note (notebook.py)
        "compact": True,  # a long conversation keeps a summary of the messages the model no longer sees (chat.compact)
        "compact_model": None,  # a cheaper model to write those summaries and titles (None: the chat's model)
        "auto_title": True,  # a conversation nobody named is titled by the model after its first answer (chat.auto_title)
        "context_tokens": None,  # the chat model's context window (None: what its server says, else 16000; chat.window)
        "compact_at": 50,  # a conversation filling this % of that window is summarised early, before the next answer
        "organise_notes": True,  # notes nobody filed are filed in PARA, or a suggestion waits on them (notebook.py)
        "untrusted_marking": True,  # third-party text reaches the model marked as data, not instructions (untrusted.py)
        "untrusted_asks": True,  # after reading it, tools that send data out or change the server ask first (ai_tools.py)
        "browser": True,  # people may connect their browser's Lens side panel for the assistant to use (browser.py)
        "browser_steps": 25,  # at most this many tool steps in an answer that uses the browser
        "answer_comments": True,  # a comment that tags @lens gets the agent's answer on its thread (comment_agent.py)
        "injection_check": "patterns",  # flag text that reads like instructions to an AI: patterns, classifier, model, off
        "disabled_tools": [],
        "max_steps": 6,
        "max_tool_calls": 50,  # the most tool calls a question may give one answer (its `max_tool_calls`; chat.tool_answer)
        "max_transcript_reads": 20,
        "confirm_over_recordings": 100,
        "confirm_over_cost": None,
        "price_in": None,
        "price_out": None,
    },
    # the assistant's web search and page reading (web.py, docs/assistant.md#web-search): off until an admin picks a
    # provider. duckduckgo needs no key (the ddgs package, fetched when it's picked); searxng asks the instance at
    # searxng_url; brave and tavily need api_key. results: per search; read_chars: of a page's text the model reads;
    # render: a page that comes back nearly empty is loaded with Chromium (scripts run), when Chromium is installed
    "web": {"search": "off", "searxng_url": None, "api_key": None, "results": 5, "read_chars": 20000, "render": False},
    # deep research (research.py, docs/assistant.md#deep-research): the most tool calls a plan may make, the minutes
    # a run may take before it writes its report with what it found, a model for it (null: the conversation's), and
    # whether an agent (an API key, an app, MCP) may start a run without the person reviewing its plan
    "research": {"max_tool_calls": 40, "max_minutes": 20, "model": None, "agents_skip_review": False},
    # talking to Lens (voice.py): input auto uses the server's speech-to-text engine when it has one, else the
    # browser's; spoken answers come from tts_model (an OpenAI-compatible /audio/speech), else the browser reads them
    # stt: the engine that hears voice chat ("same": the transcription engine); tts_provider: who reads answers aloud
    # (openai: the OpenAI-compatible speech server below; elevenlabs, deepgram, gemini: the keys in speech; say: macOS's
    # own voices, when Lens runs on a Mac itself; kokoro: Kokoro v1.0 on this machine, fetched once it's picked, its
    # model kokoro_model (fp32 or int8); styletts2: the optional service at styletts2_url); tts_voice: the voice
    # they read in (null: the provider's default)
    "voice": {
        "input": "auto",
        "stt": "same",
        "tts_provider": "openai",
        "tts_base_url": None,
        "tts_model": None,
        "tts_voice": None,
        "tts_api_key": None,
        "kokoro_model": "fp32",
        "styletts2_url": "http://styletts2:8880",
    },
    # podcast episodes made from what someone picked (domain/podcasts.py): where they go, the hosts' names, how much
    # source text the model reads (characters), the longest episode (minutes), the speaking pace, and a model for
    # its LLM calls (null: llm.model). voice_a and voice_b are the hosts' text-to-speech voices (null: voice.tts_voice,
    # and a second voice of the provider's)
    "podcasts": {
        "namespace": "podcasts",
        "host_a": "Alex",
        "host_b": "Sam",
        "voice_a": None,
        "voice_b": None,
        "context_chars": 24000,
        "max_minutes": 30,
        "words_per_minute": 150,
        "model": None,
    },
    # a chat model Lens runs itself with llama.cpp (domain/local_llm.py): model is a catalog id or
    # "hf:<owner>/<repo>/<file>.gguf"; server is llama-server's path (a startup setting; else PATH, else fetched)
    "local_llm": {
        "enabled": False,
        "model": None,
        "use_as_provider": True,
        "context": 4096,
        "threads": None,
        "gpu_layers": 999,
        "port": 8091,
        "host": None,
        "server": None,
    },
    # speech providers (domain/speech.py): transcription, speakers and text to speech by a service instead of this
    # server, picked in transcribe.engine, diarize.engine and voice. A base URL can be a proxy or a compatible server.
    # {p}_max_minutes and {p}_max_mb are the most audio a provider takes in one request: a longer recording is cut into
    # pieces at pauses and their transcripts joined (docs/speech-providers.md, Long recordings, says where each comes from)
    "speech": {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_model": "whisper-1",
        "openai_api_key": None,
        "openai_max_minutes": 10,  # 25 MB a file; GPT-4o transcription takes up to 25 minutes
        "openai_max_mb": 25,
        "elevenlabs_base_url": "https://api.elevenlabs.io",
        "elevenlabs_model": "scribe_v1",
        "elevenlabs_api_key": None,
        "elevenlabs_max_minutes": 600,  # up to 10 hours and 3 GB
        "elevenlabs_max_mb": 3000,
        "assemblyai_base_url": "https://api.assemblyai.com",
        "assemblyai_model": "universal",
        "assemblyai_api_key": None,
        "assemblyai_max_minutes": 600,  # up to 10 hours; 2.2 GB through its upload endpoint
        "assemblyai_max_mb": 2200,
        "deepgram_base_url": "https://api.deepgram.com",
        "deepgram_model": "nova-3",
        "deepgram_api_key": None,
        "deepgram_max_minutes": 120,  # 2 GB; it stops a request after 10 minutes of work, so two hours at a time
        "deepgram_max_mb": 2000,
        "gemini_base_url": "https://generativelanguage.googleapis.com",
        "gemini_model": "gemini-2.5-flash-preview-tts",
        "gemini_api_key": None,
        "sentiment": True,
        "timeout": 1800,
    },
    # the assistant in chat rooms through Matterbridge (bridge.py): url is its API (http://matterbridge:4242), token its
    # API token; it answers as `account` (an email), when a message names it (answer "mention") or to every message
    # ("all"), from anyone or only the chat usernames in `users`, in every gateway or only `gateway`; `rooms` gives a
    # gateway (or gateway/channel) to a namespace's own assistant, as "gateway = namespace" (ns_assistant.py)
    "bridge": {
        "enabled": False,
        "url": None,
        "token": None,
        "gateway": None,
        "account": None,
        "name": "Lens",
        "answer": "mention",
        "users": [],
        "rooms": [],
        # approving the assistant's reversible changes (entity merges and edits, new namespaces) by replying in the room,
        # for the people in bridge.users; everything else is approved in the web app (bridge.ROOM_SAFE)
        "approve": "low_risk",
        # web addresses the answer built itself are broken up (hxxps://evil[.]example), so the room's app doesn't fetch
        # them to preview: an injection could fill one with what the answer read (bridge.safe_links)
        "safe_links": True,
        "poll_seconds": 2,
    },
    # the assistant in Telegram through a bot of your own (telegram.py), with the room settings bridge has: account,
    # name, answer, users (Telegram usernames or user ids), rooms ("<chat id> = namespace") and approve
    "telegram": {
        "enabled": False,
        "token": None,
        "account": None,
        "name": "Lens",
        "answer": "mention",
        "users": [],
        "rooms": [],
        "approve": "low_risk",
        # a Bot API server of your own (telegram-bot-api), else Telegram's
        "api_url": None,
    },
    # Slack through an app of your own over Socket Mode (slack.py), made from the manifest in Settings › Slack:
    # bot_token is its xoxb- token, app_token its app-level xapp- token (connections:write); the room settings are
    # bridge's: account, name, answer, users (Slack usernames or ids), rooms ("slack/<channel> = namespace"), approve
    "slack": {
        "enabled": False,
        "bot_token": None,
        "app_token": None,
        "account": None,
        "name": "Lens",
        "answer": "mention",
        "users": [],
        "rooms": [],
        "approve": "low_risk",
    },
    # the assistant's own email address (mailbox.py): mail sent or forwarded to it is answered by email and what's
    # attached imported, with the room settings bridge has: account, name, users (the addresses it answers) and
    # approve. The servers are guessed from the address when not set; the ports follow the security when not set.
    "mailbox": {
        "enabled": False,
        "address": None,
        "password": None,
        "username": None,  # the sign-in name, when it isn't the address
        "imap_host": None,
        "imap_port": None,
        "imap_security": "ssl",
        "smtp_host": None,
        "smtp_port": None,
        "smtp_security": "starttls",
        "folder": "INBOX",
        "account": None,
        "name": "Lens",
        "users": [],
        # "from: *@acme.com = acme", "subject: newsletter = skip" (mail_routes.py's conditions): the namespace mail is for
        "routes": [],
        "approve": "low_risk",
        "imports": "auto",  # what's attached is imported without waiting for approval ("ask": it waits)
        # whose mail counts as theirs: what the receiving server authenticated (DKIM, SPF, DMARC), or From as it is
        "check": "authenticated",
        "auth_server": None,  # the server whose Authentication-Results count (else the topmost)
        "poll_seconds": 30,
    },
    # Matterbridge run by Lens (matterbridge.py): with `run` on, the matterbridge service in Docker Compose gets its
    # config from these, a gateway per room, and the bridge talks to it; off, bridge.url is a Matterbridge of your own
    "matterbridge": {
        "run": False,
        "slack_token": None,
        "slack_channels": [],
        "discord_token": None,
        "discord_server": None,
        "discord_channels": [],
        "telegram_token": None,
        "telegram_chats": [],
        "matrix_server": None,
        "matrix_login": None,
        "matrix_password": None,
        "matrix_rooms": [],
        "whatsapp_number": None,
        "whatsapp_groups": [],
    },
    # what Lens fetches for itself (components.py): auto fetches what the settings need, on first use unless ahead;
    # also names optional ones (fetched straight away)
    # hf_token: a Hugging Face token, for gated models (pyannote, some GGUF models); kept encrypted, HF_TOKEN wins
    "components": {"auto": True, "ahead": False, "also": [], "hf_token": None},
    # routine decisions the assistant takes instead of asking (decide.py): engine auto uses the decision model when it
    # has a key, else the language model. act_above: the confidence it acts on; below it, it asks. Engine laya runs a
    # Laya model (laya_model) on this Mac, or asks a Laya server (laya_url, `lens decide-server`).
    "decisions": {
        "engine": "auto",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-latest",
        "api_key": None,
        "act_above": 0.8,
        "timeout": 10,
        "price_per_call": None,  # USD per decision, for the activity ledger's cost (none: not counted)
        "laya_model": "aac6fef/laya-mlx",
        "laya_url": None,
    },
    "video": {
        "sample_seconds": 5,
        "scene_threshold": 0.3,
        "min_shot_seconds": 1.0,
        "frame_width": 960,
        "ocr_engine": "auto",
        "ocr_languages": ["eng"],
        "ocr_min_confidence": 60,
        "face_engine": "opencv",
        "yunet_model": None,
        "sface_model": None,
        "face_cluster_threshold": 0.6,
        "face_match_threshold": 0.45,
        "face_review_threshold": 0.3,
        "publish_faces": False,
        "object_engine": "yolox",
        "yolox_model": None,
        "ultralytics_model": None,
        "object_min_score": 0.4,
        # playable copies (playable.py): off by default, since transcoding a long video takes a small machine a while
        "playable_copies": False,
        "playable_height": 720,
    },
    # NSFW classification (nsfw.py, docs/nsfw.md): off until an admin turns it on here, or an owner for their namespace
    # (which can also choose its own threshold and display). A local model (ViT, Apache-2.0, fetched on first use) scores
    # each picture; a resource is flagged when one scores at least the threshold. display is how flagged resources show
    # in the web app: blur (click to see), hide (left out of the Library unless asked for) or show.
    "nsfw": {"enabled": False, "threshold": 0.8, "display": "blur", "max_frames": 300},
    # enhancing low quality images before they're read (enhance.py, docs/enhance.md): off until an admin turns it on.
    # An image smaller than min_side (its shorter side, in pixels), blurrier than min_sharpness (the variance of its
    # Laplacian), noisy, dull, or with a face under min_face_px tall gets an enhanced copy, at most max_scale times
    # bigger, which the steps after it read; the original stays. image_engine: classic (Pillow, nothing to fetch) or
    # realesrgan (Real-ESRGAN general x4v3, BSD-3-Clause, 5 MB, fetched on first use). only_faces: only pictures with a
    # face, where the namespace finds faces.
    "enhance": {
        "images": False,
        "image_engine": "classic",
        "only_faces": False,
        "min_side": 720,
        "min_sharpness": 100,
        "min_face_px": 64,
        "max_scale": 4,
    },
    # rclone and local_roots are bootstrap-only on purpose: the web app must not be able to pick an executable
    # or open up arbitrary folders on the server. Local folders can only be watched inside local_roots.
    "sources": {"rclone": None, "local_roots": [], "check_seconds": 15, "cache_dir": None},
    "reports": {"audio": "link"},
    # the read-only SMB share of the file tree (smb_share.py, docs/smb.md): off until an admin turns it on, then
    # served by `lens worker` to people signing in with a file access key, from allowed_networks only. bind and port
    # are startup settings only: in Docker it listens on 0.0.0.0:4445 inside the container, published where the web
    # app is (LENS_SHARE_BIND, else LENS_BIND) on LENS_SMB_PORT.
    "smb": {
        "enabled": False,
        "bind": os.environ.get("LENS_SMB_LISTEN") or "127.0.0.1",
        "port": int(os.environ.get("LENS_SMB_LISTEN_PORT") or 4445),
        "allowed_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "100.64.0.0/10", "fc00::/7", "::1/128"],
        "add_files": True,  # files people copy in are imported (share_import.py); off: read-only
    },
    # the assistant's code sandbox (sandbox.py, docs/assistant.md#code-sandbox): off until an admin turns it on. Then
    # each conversation's Python and bash run in a sandbox of its own on the OpenShell gateway at `gateway`, made from
    # `image` with `cpu` and `memory`, stopped after idle_minutes and deleted keep_hours after its last run. network:
    # off (none), packages (PyPI and npm) or hosts (those and `hosts`). who: editors (and admins), or admins only. The
    # gateway's address is the one the Compose profile `sandbox` runs.
    "sandbox": {
        "enabled": False,
        "gateway": "openshell-gateway:17670",
        "image": "python:3.12-slim",
        "network": "off",
        "hosts": [],
        "who": "editors",
        "cpu": "1",
        "memory": "512Mi",
        "seconds": 60,
        "idle_minutes": 15,
        "keep_hours": 24,
    },
    # sensors (sensors.py, docs/sensors.md): off until an admin turns them on. Then the MQTT hub and the syslog listener
    # run in the process that runs routines (`lens worker`), on these ports; syslog is taken only from syslog_networks.
    # store, raw_days, rollup_days, important_days and max_per_minute are what a stream sensor gets unless it has its own.
    # bind is a startup setting only. mqtt_web: the broker over WebSockets too, on mqtt_web_port, which the web app
    # relays at /mqtt (so Remote access and HTTPS reach it), for clients with a hub login only.
    "sensors": {
        "enabled": False,
        "bind": "0.0.0.0",
        "mqtt": True,
        "mqtt_port": 1883,
        "mqtt_anonymous": False,
        "mqtt_web": False,
        "mqtt_web_port": 9883,
        "syslog": True,
        "syslog_port": 5514,
        "syslog_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128"],
        "max_payload_kb": 256,
        "store": "all",
        "raw_days": 30,
        "rollup_days": 365,
        "important_days": 180,
        "max_per_minute": 600,
        "triage": False,
    },
    # the file tree over NFS (domain/nfs_share.py, docs/file-tree.md): off until turned on; read-only, shown as `account`
    # (an account id; null: the first admin), to the machines in allowed_networks. bind is a startup setting only.
    "nfs": {
        "enabled": False,
        "bind": "0.0.0.0",
        "port": 2049,
        "allowed_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128"],
        "account": None,
        "add_files": True,  # files written to the share are imported, as `account` (share_import.py); off: read-only
    },
    # the graph as files over S3 and WebDAV (shares.py, docs/file-tree.md): off until an admin turns it on. Then the process
    # that runs routines (`lens worker`) serves it on these ports; add_files imports the files people add to it
    # (share_import.py), off: read-only. bind is a startup setting only. remote: also at the web app's address (/s3, /dav),
    # so through Settings › Remote access and HTTPS too; off until an admin turns it on.
    "shares": {
        "enabled": False,
        "bind": "0.0.0.0",
        "s3": True,
        "s3_port": 9555,
        "webdav": True,
        "webdav_port": 9556,
        "add_files": True,
        "remote": False,
    },
    # notifications to webhooks and Matterbridge (docs/notifications.md): targets reach public addresses only, and the
    # private networks listed here (a Matterbridge on the LAN or the Docker network); app_url is where links in messages
    # point (null: FRONTEND_URL)
    # outgoing email (app/email.py): access requests and password resets. MAIL_* in .env set them instead, locked.
    "mail": {
        "server": None,
        "port": 587,
        "username": None,
        "password": None,
        "from_address": None,
        "from_name": "Lens",
        "security": "starttls",
    },
    # reaching Lens from the internet through a Cloudflare Tunnel run by Lens (domain/tunnel.py): mode off, quick (a
    # random trycloudflare.com address), token (a tunnel made in the Cloudflare dashboard) or managed (Lens makes the
    # tunnel and DNS record for hostname with api_token). origin: the web app as this server reaches it (default
    # LENS_TUNNEL_ORIGIN, else FRONTEND_URL). token and api_token are secrets.
    "tunnel": {"mode": "off", "hostname": "", "token": None, "api_token": None, "origin": ""},
    # Lens's own private network between its machines, a Nebula mesh (domain/mesh.py, docs/mesh.md). Off until an admin
    # adds a node. network: the mesh's addresses (the primary is the first); port: the UDP port the primary listens on;
    # hosts: where other machines reach that port from outside the mesh (host or host:port; none: the address each
    # joined at); cipher: aes or chachapoly, the same on every machine; cert_days: how long a machine's certificate
    # lasts (renewed at two thirds); relay: the primary relays for machines that can't reach each other directly
    "mesh": {
        "enabled": False,
        "network": "10.73.0.0/16",
        "port": 4242,
        "hosts": [],
        "cipher": "aes",
        "cert_days": 30,
        "relay": True,
        "mtu": 1300,
        "log_level": "info",
    },
    # HTTPS with certificates from Let's Encrypt or ZeroSSL, got and renewed by Caddy run by Lens (domain/https.py). Off
    # by default. domains: the names to serve (and get certificates for); ca: letsencrypt, zerossl, both (Let's Encrypt
    # then ZeroSSL) or custom (ca_url, an ACME directory); challenge: http (ports 80 and 443 reach this machine), tls-alpn
    # (only 443) or dns (a TXT record through dns_provider's API: any caddy-dns plugin, dns_package to use another one;
    # dns_credentials is its fields as JSON). eab_*: external account binding. https_port and http_port are where Caddy
    # listens in the container (install.sh publishes 443 and 80 to them). dns_credentials and eab_hmac_key are secrets.
    "https": {
        "enabled": False,
        "domains": [],
        "email": "",
        "ca": "both",
        "ca_url": "",
        "staging": False,
        "challenge": "http",
        "dns_provider": "",
        "dns_package": "",
        "dns_credentials": None,
        "dns_resolvers": [],
        "propagation": "",
        "eab_key_id": "",
        "eab_hmac_key": None,
        "redirect_http": True,
        "hsts": False,
        "origin": "",
        "https_port": 8443,
        "http_port": 8080,
        # strict_sni: only the domains get a TLS handshake (no SNI, an IP or another name is refused), and a request's
        # Host must match its SNI (else 421). client_auth: off, optional (a certificate, when given, must be from
        # client_ca) or required (mutual TLS); client_ca: PEM of the CAs that sign client certificates. min_tls: 1.2 or 1.3
        "strict_sni": False,
        "client_auth": "off",
        "client_ca": "",
        "min_tls": "1.2",
    },
    "notifications": {"enabled": True, "networks": [], "poll_seconds": 5, "max_attempts": 6, "app_url": None},
    # OpenTelemetry traces and metrics (docs/telemetry.md): off unless an admin turns it on, and sent only to the OTLP/HTTP
    # endpoint set here (e.g. a collector at http://localhost:4318). headers is a secret: key=value pairs for the
    # endpoint's auth. prices: {model: {input, output}} in USD per million tokens, for cost estimates.
    # encryption at rest (docs/encryption.md): files Lens keeps under data_dir, encrypted with their namespace's key;
    # work_minutes: how long a plain working copy for ffmpeg and the other tools is kept after its last use
    "encryption": {"files": False, "work_minutes": 30, "vault_minutes": 60},
    # secrets kept in a password manager (docs/secrets.md; domain/secret_providers.py): a secret field in Settings can
    # hold a reference like vaultwarden://Item/password instead of the secret, read when the settings are built and
    # again after cache_minutes. Vaultwarden (or Bitwarden): the server's address, the account Lens signs in as (its
    # email, API key and master password, the last two secrets) and the one folder of it Lens may read.
    "secrets": {
        "cache_minutes": 10,
        "vaultwarden_url": None,
        "vaultwarden_email": None,
        "vaultwarden_client_id": None,
        "vaultwarden_client_secret": None,
        "vaultwarden_password": None,
        "vaultwarden_folder": "Lens",
    },
    # the activity ledger (docs/activity.md): every change and call in or out, with its cost, per resource. On by
    # default; reads: log API reads (GET) too; keep_days: how long rows are kept
    "activity": {"enabled": True, "reads": False, "keep_days": 365},
    # the stream of activities automations follow (docs/automations.md): keep_days: how long activities with no row
    # of their own (the event log) are kept; poll_seconds: how often followers look when nothing wakes them
    "events": {"keep_days": 30, "poll_seconds": 5},
    # automations (domain/automations.py): enabled false stops every run and keeps everything; max_depth is how many
    # automations may follow one another (one's run starting the next); runs past keep_runs_days are dropped, each
    # automation keeping its last 20; approval_days: how long an automation's action waits for your pick before it
    # expires and nothing runs; catch_up: how late a timer (a schedule, a waiting action) may still fire after Lens
    # was off, older ones being missed
    "automations": {
        "enabled": True,
        "max_depth": 3,
        "max_runs_per_minute": 60,
        "keep_runs_days": 30,
        "approval_days": 7,
        "catch_up": "1h",
    },
    # database backups (docs/database.md, Backups): schedule is a cron expression in timezone, or none (the default)
    # for none; the newest `keep` scheduled backups are kept. connection: a storage connection (sources.py) every new
    # backup is also copied to, under folder, encrypted with a key from secret.key first (none: kept here only)
    "backups": {"schedule": None, "timezone": "UTC", "keep": 7, "connection": None, "folder": "lens-backups"},
    # auto scaling (domain/scaling.py, docs/scaling.md): off by default, and a group launches nothing until a person
    # approves its terms in the web app. tick_seconds: how often the scaler looks; join_minutes: how long a launched
    # unit may take to join before it's given up on; accrue_minutes: how often a running unit's price goes into the
    # ledger; tag: the prefix of units' names (and the tag they carry at the provider)
    "scaling": {"enabled": False, "tick_seconds": 30, "join_minutes": 15, "accrue_minutes": 5, "tag": "lens"},
    "telemetry": {
        "enabled": False,
        "endpoint": None,
        "headers": None,
        "traces": True,
        "metrics": True,
        "sample_ratio": 1.0,
        "export_seconds": 60,
        "service_name": "lens",
        "prices": {},
        # a Prometheus scrape endpoint at /metrics (docs/prometheus.md): off by default; prometheus_token is a secret
        # the scraper sends as a bearer token, and nothing is served until one is set
        "prometheus": False,
        "prometheus_token": None,
        # where Grafana shows Lens's charts (docs/prometheus.md), for a link on Admin → System health; none: no link
        "grafana_url": None,
    },
    # logging (docs/logging.md): level and format for the console (docker logs) and a file per process under
    # data_dir/logs (what Settings → Logs shows); levels sets single loggers, like {"httpx": "warning"}. syslog sends
    # every line at syslog_level or above to a syslog server (RFC 5424 over udp, tcp or tls); http sends them in batches to
    # http_url: Grafana Loki's push API (loki), a JSON array (json) or one JSON object per line (ndjson), with the sealed
    # http_headers (key=value pairs, like telemetry.headers); both off by default. client_cert and server_ca are for TLS
    "logs": {
        "level": "info",
        "format": "text",
        "levels": {},
        "file": True,
        "file_mb": 5,
        "file_keep": 2,
        "syslog": False,
        "syslog_host": None,
        "syslog_port": 514,
        "syslog_transport": "udp",
        "syslog_level": "info",
        "syslog_facility": "local0",
        "syslog_format": "text",
        "syslog_framing": "octet",
        "syslog_verify": True,
        "syslog_app_name": "lens",
        "http": False,
        "http_url": None,
        "http_kind": "loki",
        "http_level": "info",
        "http_headers": None,
        "http_verify": True,
        # for syslog over TLS and an https endpoint: this machine's certificate and key (PEM, sealed) for servers that
        # ask for one (mutual TLS), and the CA (PEM) that signed the server's certificate when it isn't a public one
        "client_cert": None,
        "server_ca": None,
    },
    # usage analytics (docs/analytics.md): the numbers are worked out locally from the activity ledger; Google Analytics
    # is off unless an admin turns it on with a GA4 measurement id and a Measurement Protocol API secret (sealed)
    "analytics": {
        "enabled": False,
        "measurement_id": None,
        "api_secret": None,
        "region": "global",
        "events": [
            "lens_install",
            "lens_setup_done",
            "lens_daily_active",
            "lens_feature_used",
            "lens_upgrade",
            "lens_inactive",
            "lens_return",
        ],
        "inactive_days": 30,
    },
    # new releases (docs/updates.md): check asks GitHub for repo's latest release every check_hours, for the web app's
    # banner; auto_install installs one as soon as it's seen, through the machine's upgrade agent (install.sh sets it up)
    # email: tell admins by email when a new release is seen (once per release; needs Settings › Email). Namespaces'
    # notification targets can get it too (the lens.release event).
    "updates": {"check": True, "check_hours": 24, "auto_install": False, "email": True, "repo": "adeelahmad/lense"},
    # Fedora (docs/fedora.md): a copy of the archive in a Fedora 6 repository, off until url is set (enabled: false pauses
    # it). password is a
    # secret; files: send recordings' files too (up to max_file_mb each, 0: any size).
    "fedora": {
        "enabled": True,
        "url": None,
        "user": None,
        "password": None,
        "root": "lens",
        "files": True,
        "max_file_mb": 0,
        "sync_seconds": 60,
        "full_hours": 24,
    },
    # IIIF: identifiers are built from base_url (set it to the stable public HTTPS address; null: the request's address)
    "iiif": {
        "base_url": None,
        "default_language": "none",
        "layers": ["transcript", "speakers", "entities", "chapters", "screen"],
        "rights": None,
        "attribution": None,
        "provider": None,
        "viewers": [],
        "allowed_origins": ["*"],
        "token_minutes": 60,
    },
}
NS_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None, overrides=None):
    path = path or os.environ.get("ARCHIVE_CONFIG") or "archive.yaml"
    p = pathlib.Path(path).expanduser()
    raw = {}
    if p.exists():
        import yaml

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = _merge(_merge(DEFAULTS, raw), overrides or {})
    base = p.resolve().parent if p.exists() else pathlib.Path.cwd()
    cfg["data_dir"] = str((base / os.path.expanduser(cfg["data_dir"])).resolve())
    nss = {}
    for name, spec in (cfg.get("namespaces") or {}).items():
        if not NS_RX.match(str(name)):
            raise SystemExit(f"namespace names use lowercase letters, digits, - and _: {name!r}")
        spec = {"paths": spec} if isinstance(spec, list) else (spec or {})
        graph = spec.get("graph", "shared")
        if graph not in ("shared", "isolated"):
            raise SystemExit(f"namespace {name}: graph must be shared or isolated")
        nss[name] = {"paths": [str((base / os.path.expanduser(x)).resolve()) for x in spec.get("paths", [])], "graph": graph}
    cfg["namespaces"] = nss
    if extra := os.environ.get("LENS_WEB_NETWORKS"):  # for Docker and the packages, whose archive.yaml is in the image
        cfg["documents"]["web_networks"] = [*(cfg["documents"].get("web_networks") or []), *extra.replace(",", " ").split()]
    if cfg["search"]["stemming"] not in ("english", "none"):
        raise SystemExit("search.stemming must be english or none")
    if cfg["search"]["engine"] not in SEARCH_ENGINES:
        raise SystemExit(f"search.engine must be one of {', '.join(SEARCH_ENGINES)}")
    cfg["_path"] = str(p)
    return cfg


def resolve_path(cfg, path):
    """Recordings scanned on one machine (say a Mac) can be served from another (say a container)."""
    for src, dst in (cfg["audio"].get("path_map") or {}).items():
        if path and path.startswith(src):
            return dst + path[len(src) :]
    return path


# ---------- SurrealDB ----------
def R(table, key):
    from surrealdb import RecordID

    return RecordID(table, key)


def _plain(v):
    from surrealdb import RecordID

    if isinstance(v, RecordID):
        return v.id
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


RETRIES = 8


def _retryable(err):
    """SurrealDB's optimistic transactions fail with a write conflict when two touch the same record at once, and ask
    for a retry when a transaction's snapshot is older than the commit oracle's GC window (a busy server)."""
    m = str(err)
    return "can be retried" in m or "Transaction conflict" in m or "Write conflict" in m or "retry required" in m


def _lost(err):
    """The connection dropped: the statement may not have been sent (or answered), and the connection is unusable."""
    import websockets

    from surrealdb.errors import ConnectionUnavailableError

    return isinstance(err, (websockets.exceptions.ConnectionClosed, ConnectionUnavailableError, ConnectionError))


def _closed(c):
    """A server connection whose websocket has closed (embedded and HTTP connections have none)."""
    from websockets.protocol import State

    sock = getattr(c, "socket", None)
    return sock is not None and sock.protocol.state in (State.CLOSING, State.CLOSED)


def _backoff(attempt):
    time.sleep(min(0.5, 0.005 * 2**attempt) * (0.5 + random.random()))


def _released(folder, wait=60.0):
    """Wait (up to `wait` seconds) until this process has no file under `folder` open: a closed embedded engine lets
    go of its files when it is collected, and copying or replacing them before then mixes old writes into the new."""
    import gc

    gc.collect()
    root = str(pathlib.Path(folder).resolve()) + os.sep
    fds = pathlib.Path("/proc/self/fd")
    if not fds.is_dir():  # not Linux: collecting is all that can be done
        return
    until = time.monotonic() + wait
    while time.monotonic() < until:
        held = False
        for fd in list(fds.iterdir()):
            with contextlib.suppress(OSError):
                if os.readlink(fd).startswith(root):
                    held = True
                    break
        if not held:
            return
        gc.collect()
        time.sleep(0.02)
    raise RuntimeError(f"the embedded database still had files open {wait:.0f} s after closing it; try again")


class DB:
    """A thread-safe SurrealDB handle.

    Embedded engines (surrealkv://, mem://) allow one connection per process, so every query waits for it.
    A SurrealDB server (ws://, http://) gets a small pool of connections, so concurrent requests don't queue behind
    each other. Each query() call runs one statement on one connection; run() sends a transaction on one connection.
    Statements that lose a write conflict to a concurrent one (SurrealDB reports these as retryable) are retried.
    """

    def __init__(self, cfg, pool_size=None):
        d = cfg["database"]
        url = os.environ.get("SURREAL_URL") or d.get("url") or "surrealkv://" + str(pathlib.Path(cfg["data_dir"]) / "surrealdb")
        self.url = url
        self.data_dir = cfg.get("data_dir")
        scheme = url.split(":", 1)[0]
        self.embedded = scheme in ("mem", "memory", "surrealkv", "file")
        if scheme in ("surrealkv", "file"):
            pathlib.Path(url.split("://", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self._creds = {
            "username": os.environ.get("SURREAL_USER") or d.get("user") or "root",
            "password": os.environ.get("SURREAL_PASS") or d.get("password") or "root",
        }
        self._target = (os.environ.get("SURREAL_NS") or d["namespace"], os.environ.get("SURREAL_DB") or d["database"])
        if os.environ.get("SURREAL_AUTH") == "database":  # a user of this one database (a worker node's: docs/mesh.md)
            self._creds.update(namespace=self._target[0], database=self._target[1])
        size = 1 if self.embedded else max(1, int(pool_size or os.environ.get("SURREAL_POOL_SIZE") or d.get("pool_size") or 8))
        self.fulltext = None  # FULLTEXT (3.x), SEARCH (2.x) or None; set by connect()
        self.textindex = None  # the SQLite full-text index (textindex.py) when search.engine is sqlite
        self._text_ready, self._text_lock = not self.embedded, threading.Lock()
        self._pool: queue.LifoQueue = queue.LifoQueue()
        self._all = []
        # one connection now, the rest of the pool as concurrent queries need them: each server sign-in costs time
        self._size, self._grow_lock = size, threading.Lock()
        self._held = threading.local()  # the connection the current thread is using, if any (conn)
        try:
            c = self._open()
            self._all.append(c)
            self._pool.put(c)
        except Exception as e:  # noqa: BLE001
            self.close()
            if self.embedded:
                hint = " If another process has this embedded database open, stop it or point both at a SurrealDB server (SURREAL_URL=ws://...)."
            elif "authentication" in str(e).lower():
                # the server keeps the root user it was first started with: a later SURREAL_PASS doesn't change it
                hint = (
                    " SURREAL_USER/SURREAL_PASS don't match the user this database was created with."
                    " Set them back to the values it was first started with (see .env), then restart."
                )
            else:
                hint = ""
            raise SystemExit(f"cannot open SurrealDB at {url}: {e}.{hint}") from None

    def _open(self):
        from surrealdb import Surreal

        attempt = 0
        while True:
            c = Surreal(self.url)
            try:
                if not self.embedded:
                    c.signin(self._creds)
                c.use(*self._target)  # on a server this creates the database, and two at once can conflict
                return c
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    c.close()
                if attempt == RETRIES or not _retryable(e):
                    raise
                _backoff(attempt)
                attempt += 1

    @contextlib.contextmanager
    def closed(self):
        """Every connection closed meanwhile (once the queries in flight finish), then opened again: for copying an
        embedded database's files."""
        held = [self._pool.get() for _ in self._all]
        n = len(held)
        for c in held:
            with contextlib.suppress(Exception):
                c.close()
            # the embedded engine keeps its files open, and may still write them, until its handle is dropped
            with contextlib.suppress(AttributeError):
                c._db = None
        del c
        # the places stay taken, so a query meanwhile waits for the pool instead of opening a connection of its own
        held, self._all = [], [None] * n
        try:
            if self.embedded:
                _released(self.url.partition("://")[2])
            yield
        finally:
            for i in range(n):
                c = self._open()
                self._all[i] = c
                self._pool.put(c)

    @contextlib.contextmanager
    def conn(self):
        """A connection from the pool. A server connection that has dropped (the server restarted, or a keepalive
        ping went unanswered while the machine slept) is replaced: without that, every query sent on it fails until
        the process restarts.

        A thread already inside a query gets its own connection again when the engine is embedded: the garbage
        collector closes an abandoned streamed answer (its reader left) on whatever thread it runs on, mid-query, and
        that answer's cleanup queries too; with the embedded engine's one connection held by that very thread, waiting
        for it would hang the whole server. While Python runs, the outer query is between sends, so the inner one is
        safe on it. A server connection is not shared: the SDK sends on it under a lock that the same thread cannot
        take twice, so there the inner query takes another connection from the pool."""
        outer = getattr(self._held, "c", None)
        if outer is not None and self.embedded:
            yield outer
            return
        c = self._take()
        try:
            if _closed(c):
                c = self._replace(c)
            self._held.c = c
            yield c
        except Exception as e:
            if not self.embedded and _lost(e):
                c = self._replace(c)
            raise
        finally:
            self._held.c = outer
            self._pool.put(c)

    def _take(self):
        """An idle connection; a new one while the pool is below its size; otherwise wait for one to come back."""
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._grow_lock:
            grow = len(self._all) < self._size
            if grow:
                self._all.append(None)  # the place is taken while the new connection opens
        if not grow:
            return self._pool.get()
        try:
            c = self._open()
        except Exception:
            with self._grow_lock:
                self._all.remove(None)
            raise
        with self._grow_lock:
            self._all[self._all.index(None)] = c
        return c

    def _replace(self, old):
        """A fresh connection in place of a dropped one; the dropped one when the server can't be reached (the next
        query tries again)."""
        try:
            new = self._open()
        except Exception:  # noqa: BLE001
            logging.getLogger("lens").warning("SurrealDB at %s is unreachable; will retry on the next query", self.url, exc_info=True)
            return old
        with contextlib.suppress(Exception):
            old.close()
        self._all = [new if x is old else x for x in self._all]
        logging.getLogger("lens").warning("SurrealDB connection dropped; reconnected to %s", self.url)
        return new

    def q(self, sql, **v):
        for attempt in range(RETRIES + 1):
            try:
                with self.conn() as c:
                    return c.query(sql, v)
            except Exception as e:  # noqa: BLE001
                if attempt == RETRIES or not _retryable(e):
                    raise
                _backoff(attempt)

    def rows(self, sql, **v):
        r = self.q(sql, **v)
        if r is None:
            return []
        return [_plain(x) for x in (r if isinstance(r, list) else [r])]

    def one(self, sql, **v):
        r = self.rows(sql, **v)
        return r[0] if r else None

    def values(self, sql, **v):
        r = self.q(sql, **v)
        return [_plain(x) for x in (r if isinstance(r, list) else [r])] if r is not None else []

    def run(self, statements, transaction=True, **v):
        """Several statements in one round trip, as one transaction unless transaction=False; raises if any fails."""
        sql = ";\n".join(statements) + ";"
        if transaction:
            sql = "BEGIN TRANSACTION;\n" + sql + "\nCOMMIT TRANSACTION;"
        for attempt in range(RETRIES + 1):
            with self.conn() as c:
                raw = c.query_raw(sql, v)
            items = raw.get("result") if isinstance(raw, dict) else raw
            if isinstance(raw, dict) and raw.get("error"):
                if attempt < RETRIES and _retryable(raw["error"]):
                    _backoff(attempt)
                    continue
                raise RuntimeError(raw["error"])
            errors = [
                (n, str(it.get("result")))
                for n, it in enumerate(items or [])
                if isinstance(it, dict) and it.get("status") not in (None, "OK")
            ]
            if errors and attempt < RETRIES and any(_retryable(m) for _, m in errors):
                _backoff(attempt)  # the whole transaction was rolled back, so running it again is safe
                continue
            break
        if errors:  # report the statement that failed, not the ones skipped because of it
            n, msg = next((e for e in errors if "not executed due to a failed transaction" not in e[1]), errors[0])
            n -= 1 if transaction else 0  # BEGIN TRANSACTION is the first result
            stmt = statements[n] if 0 <= n < len(statements) else "?"
            raise RuntimeError(f"query failed ({stmt[:80]}): {msg[:300]}")

    def next_id(self, table):
        return int(self.values("UPSERT $r SET n += 1 RETURN VALUE n", r=R("seq", table))[0])

    def ready_fulltext(self):
        """The full-text index kind (FULLTEXT or SEARCH) once it can be trusted; None when there is none.

        The embedded engine (SurrealDB 2.x inside the Python SDK) loses postings from its SEARCH index when the
        database is closed and reopened: after a restart, searches silently miss most segments. So the first
        full-text query in each process rebuilds the index; writes made after that are indexed correctly. SurrealDB
        servers don't have the problem, so this is a no-op for them. Callers run full-text queries only through this.
        """
        if self.fulltext and not self._text_ready:
            with self._text_lock:
                if not self._text_ready:
                    t = time.time()
                    for index, table in TEXT_INDEXES:
                        self.q(f"REBUILD INDEX IF EXISTS {index} ON {table}")
                    self._text_ready = True
                    logging.getLogger("lens").info("rebuilt the embedded full-text index in %.1fs", time.time() - t)
        return self.fulltext

    def ping(self):
        self.q("RETURN 1")
        return True

    def close(self):
        if self.textindex is not None:
            self.textindex.close()
        for c in self._all:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        self._all = []


SCHEMA = [
    # counters (next_id), the old migration counter and the settings version. Defined up front: SurrealDB 3 refuses to
    # SELECT from a table nobody has written to yet ("table 'seq' does not exist"), which a fresh database with no
    # namespaces in archive.yaml would otherwise hit in migrate() before anything had created it.
    "DEFINE TABLE IF NOT EXISTS seq SCHEMALESS",
    # data upgrades (domain/migrations.py): migration:⟨name⟩ for each step run or failed, and the lock while one runs
    "DEFINE TABLE IF NOT EXISTS migration SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS migration_lock SCHEMALESS",
    # first-run setup (domain/setup.py): setup:wizard while the web wizard is still to be finished
    "DEFINE TABLE IF NOT EXISTS setup SCHEMALESS",
    # No composite indexes: on SurrealDB 2.x a (space, x) index makes "space = $s" lookups return nothing, so
    # uniqueness is enforced on single "<space>:<value>" key fields instead.
    "DEFINE TABLE IF NOT EXISTS space SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS space_name ON space FIELDS name UNIQUE",
    # a namespace's collections (domain/hierarchy.py): every recording lives in one; key is "<space>:<parent>:<name>"
    "DEFINE TABLE IF NOT EXISTS collection SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS collection_space ON collection FIELDS space",
    "DEFINE INDEX IF NOT EXISTS collection_parent ON collection FIELDS parent",
    "DEFINE INDEX IF NOT EXISTS collection_key ON collection FIELDS key UNIQUE",
    # roles people were given on collections (hierarchy.give): id "<collection>-<account>"
    "DEFINE TABLE IF NOT EXISTS collection_role SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS collection_role_account ON collection_role FIELDS account",
    "DEFINE INDEX IF NOT EXISTS collection_role_collection ON collection_role FIELDS collection",
    "DEFINE TABLE IF NOT EXISTS recording SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS recording_space ON recording FIELDS space",
    "DEFINE INDEX IF NOT EXISTS recording_collection ON recording FIELDS collection",
    "DEFINE INDEX IF NOT EXISTS recording_fp ON recording FIELDS fp_key UNIQUE",
    "DEFINE INDEX IF NOT EXISTS recording_path ON recording FIELDS path",
    "DEFINE INDEX IF NOT EXISTS recording_status ON recording FIELDS status",
    "DEFINE INDEX IF NOT EXISTS recording_access ON recording FIELDS access",
    "DEFINE INDEX IF NOT EXISTS recording_featured ON recording FIELDS featured",
    "DEFINE TABLE IF NOT EXISTS segment SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS segment_rec ON segment FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS segment_space ON segment FIELDS space",
    "DEFINE INDEX IF NOT EXISTS segment_speaker ON segment FIELDS speaker",
    "DEFINE TABLE IF NOT EXISTS speaker SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS speaker_space ON speaker FIELDS space",
    "DEFINE INDEX IF NOT EXISTS speaker_label ON speaker FIELDS label_key UNIQUE",
    "DEFINE TABLE IF NOT EXISTS appearance SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS appearance_rec ON appearance FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS appearance_spk ON appearance FIELDS speaker",
    "DEFINE TABLE IF NOT EXISTS same_as TYPE RELATION IN speaker OUT speaker",
    "DEFINE TABLE IF NOT EXISTS suggestion SCHEMALESS",
    # pairs of speakers someone said aren't the same person: never suggested again (not_same:⟨a-b⟩, a < b)
    "DEFINE TABLE IF NOT EXISTS not_same SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS merge SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS section SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS section_rec ON section FIELDS recording",
    # the dates a recording's lines give, as days on the calendar (dates.py)
    "DEFINE TABLE IF NOT EXISTS date_said SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS date_said_rec ON date_said FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS date_said_day ON date_said FIELDS space, day",
    "DEFINE TABLE IF NOT EXISTS entity SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_space ON entity FIELDS space",
    "DEFINE INDEX IF NOT EXISTS entity_key ON entity FIELDS ekey UNIQUE",
    # how a namespace (or one of its collections) organises its entities, and its own entity types (entity_setup.py)
    "DEFINE TABLE IF NOT EXISTS entity_scope SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_scope_space ON entity_scope FIELDS space",
    "DEFINE TABLE IF NOT EXISTS entity_kind SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_kind_space ON entity_kind FIELDS space",
    "DEFINE TABLE IF NOT EXISTS mentions TYPE RELATION IN segment OUT entity",
    "DEFINE INDEX IF NOT EXISTS mentions_rec ON mentions FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS mentions_space ON mentions FIELDS space",
    "DEFINE INDEX IF NOT EXISTS mentions_entity ON mentions FIELDS entity",
    "DEFINE TABLE IF NOT EXISTS term SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS term_rec ON term FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS term_space ON term FIELDS space",
    "DEFINE INDEX IF NOT EXISTS term_term ON term FIELDS term",
    # each namespace's controlled vocabulary of topics, and which recordings are about which topic (app/domain/topics.py)
    "DEFINE TABLE IF NOT EXISTS topic SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS topic_space ON topic FIELDS space",
    "DEFINE INDEX IF NOT EXISTS topic_key ON topic FIELDS tkey UNIQUE",
    "DEFINE TABLE IF NOT EXISTS topic_about SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS topic_about_rec ON topic_about FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS topic_about_topic ON topic_about FIELDS topic",
    "DEFINE INDEX IF NOT EXISTS topic_about_space ON topic_about FIELDS space",
    # merges and deletes of topics, kept so they can be undone (app/domain/topics.py)
    "DEFINE TABLE IF NOT EXISTS topic_change SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS topic_change_space ON topic_change FIELDS space",
    # Note: on 2.x, CONTAINS against an indexed field also returns nothing; use string::contains() there.
    # settings, people and access
    "DEFINE TABLE IF NOT EXISTS app_setting SCHEMALESS",
    # the Cloudflare tunnel: which process runs cloudflared, and what it's doing (app/domain/tunnel.py); HTTPS: which
    # process runs Caddy (app/domain/https.py)
    "DEFINE TABLE IF NOT EXISTS app_service SCHEMALESS",
    # the mesh between Lens's machines (domain/mesh.py, docs/mesh.md): its CA (mesh_ca:current, the key sealed), each
    # machine on it (mesh_node:<n>, the primary among them) and the tokens machines join with (mesh_token:<n>, sealed)
    "DEFINE TABLE IF NOT EXISTS mesh_ca SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS mesh_node SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS mesh_node_role ON mesh_node FIELDS role",
    "DEFINE TABLE IF NOT EXISTS mesh_token SCHEMALESS",
    # each namespace's data key, wrapped by the keys that can open it (app/domain/keyring.py)
    "DEFINE TABLE IF NOT EXISTS data_key SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS account SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS account_email ON account FIELDS email UNIQUE",
    "DEFINE TABLE IF NOT EXISTS role SCHEMALESS",  # custom roles (roles.py), keyed by name
    "DEFINE TABLE IF NOT EXISTS membership SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS membership_account ON membership FIELDS account",
    "DEFINE INDEX IF NOT EXISTS membership_space ON membership FIELDS space",
    # permission on one recording for someone without a role in its namespace (docs/access.md): permission:<rid>-<account>
    "DEFINE TABLE IF NOT EXISTS permission SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS permission_account ON permission FIELDS account",
    "DEFINE INDEX IF NOT EXISTS permission_recording ON permission FIELDS recording",
    # someone asking for permission on a recording: access_request:<rid>-<account>, the latest request only
    "DEFINE TABLE IF NOT EXISTS access_request SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS access_request_recording ON access_request FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS access_request_status ON access_request FIELDS status",
    # the path, fingerprint and remote file of a recording deleted from a namespace or moved out of it, which that
    # namespace's scans and watched folders skip (domain/deletion.py)
    "DEFINE TABLE IF NOT EXISTS gone_recording SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS gone_recording_space ON gone_recording FIELDS space",
    # address ranges whose visitors see all of a namespace's recordings, or chosen ones (docs/access.md): ip_group:<n>
    "DEFINE TABLE IF NOT EXISTS ip_group SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS ip_group_space ON ip_group FIELDS space",
    # addresses other services push files, web addresses or text to, into one namespace (domain/import_hooks.py):
    # import_hook:<n>, found by the hash of its token
    "DEFINE TABLE IF NOT EXISTS import_hook SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS import_hook_space ON import_hook FIELDS space",
    "DEFINE INDEX IF NOT EXISTS import_hook_token ON import_hook FIELDS token_hash UNIQUE",
    # audio and video arriving in pieces (docs/api.md, Uploads): upload:<random id>
    "DEFINE TABLE IF NOT EXISTS upload SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS upload_account ON upload FIELDS account",
    "DEFINE TABLE IF NOT EXISTS login_session SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS login_session_account ON login_session FIELDS account",
    "DEFINE INDEX IF NOT EXISTS login_session_sid ON login_session FIELDS sid",
    "DEFINE TABLE IF NOT EXISTS password_reset SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS password_reset_account ON password_reset FIELDS account",
    # passkeys (domain/passkeys.py): passkey:<hash of the credential id>, the challenges being answered
    # (webauthn_flow:<hash>), one-time tickets the web app swaps for a session (login_ticket:<hash>) and links for adding
    # a passkey (signin_link:<hash>)
    "DEFINE TABLE IF NOT EXISTS passkey SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS passkey_account ON passkey FIELDS account",
    "DEFINE TABLE IF NOT EXISTS webauthn_flow SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS login_ticket SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS signin_link SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS signin_link_account ON signin_link FIELDS account",
    # signing in with an outside account (domain/external_login.py): login_provider:<key> (sealed client secret),
    # external_identity:<hash of provider|subject> (which Lens account it is) and external_flow:<hash of state>
    "DEFINE TABLE IF NOT EXISTS login_provider SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS external_identity SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS external_identity_account ON external_identity FIELDS account",
    "DEFINE INDEX IF NOT EXISTS external_identity_provider ON external_identity FIELDS provider",
    "DEFINE TABLE IF NOT EXISTS external_flow SCHEMALESS",
    # access keys for the file tree's S3 server (s3.py): id is the key id, the secret sealed
    "DEFINE TABLE IF NOT EXISTS file_key SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS file_key_account ON file_key FIELDS account",
    "DEFINE TABLE IF NOT EXISTS api_token SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS api_token_hash ON api_token FIELDS hash UNIQUE",
    "DEFINE INDEX IF NOT EXISTS api_token_account ON api_token FIELDS account",
    # OAuth (app/domain/oauth.py): apps that registered, one-time codes, the access people gave them, and its tokens
    # (oauth_client:<client id>; oauth_code and oauth_token by the hash of the code or token)
    "DEFINE TABLE IF NOT EXISTS oauth_client SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS oauth_code SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS oauth_grant SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS oauth_grant_account ON oauth_grant FIELDS account",
    "DEFINE TABLE IF NOT EXISTS oauth_token SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS oauth_token_gid ON oauth_token FIELDS gid",
    "DEFINE TABLE IF NOT EXISTS share_link SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS share_link_rec ON share_link FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS share_link_short ON share_link FIELDS short",
    # sites whose pages framed a share link's player (share_embed:⟨link-site⟩): opens and when
    "DEFINE TABLE IF NOT EXISTS share_embed SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS share_embed_rec ON share_embed FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS share_embed_share ON share_embed FIELDS share",
    "DEFINE TABLE IF NOT EXISTS audit_log SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS audit_at ON audit_log FIELDS at",
    "DEFINE INDEX IF NOT EXISTS audit_target ON audit_log FIELDS target",
    # what happened to each resource and what it cost (domain/activity.py): a row per call in, call out and run
    "DEFINE TABLE IF NOT EXISTS activity SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS activity_at ON activity FIELDS at",
    "DEFINE INDEX IF NOT EXISTS activity_resources ON activity FIELDS resources",
    # caps on what a routine, pipeline, workflow or namespace may cost (domain/budgets.py): budget:<table>_<id>
    "DEFINE TABLE IF NOT EXISTS budget SCHEMALESS",
    # auto scaling (domain/scaling.py): providers with their credentials sealed, groups with their rules and approval,
    # and each unit launched (a worker machine or a model endpoint), alive or gone
    "DEFINE TABLE IF NOT EXISTS scale_provider SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS scale_group SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS scale_group_provider ON scale_group FIELDS provider",
    "DEFINE TABLE IF NOT EXISTS scale_node SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS scale_node_group ON scale_node FIELDS group",
    "DEFINE INDEX IF NOT EXISTS scale_node_state ON scale_node FIELDS state",
    # background work
    "DEFINE TABLE IF NOT EXISTS job SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_status ON job FIELDS status",
    "DEFINE INDEX IF NOT EXISTS job_rec ON job FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS job_updated ON job FIELDS updated_at",
    "DEFINE INDEX IF NOT EXISTS job_finished ON job FIELDS finished_at",
    "DEFINE INDEX IF NOT EXISTS recording_created ON recording FIELDS created_at",
    # notifications (domain/notify.py): a namespace's targets (notify_target:<n>), what each was sent
    # (notify_delivery:<random>), the events claimed for sending (notify_event:<hash of its key>) and where the
    # notifier's next look starts (notify_state:scan)
    "DEFINE TABLE IF NOT EXISTS notify_target SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS notify_target_space ON notify_target FIELDS space",
    "DEFINE TABLE IF NOT EXISTS notify_delivery SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS notify_delivery_target ON notify_delivery FIELDS target",
    "DEFINE INDEX IF NOT EXISTS notify_delivery_status ON notify_delivery FIELDS status",
    "DEFINE TABLE IF NOT EXISTS notify_event SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS notify_state SCHEMALESS",
    # the stream of activities (domain/events.py, docs/automations.md): activities no row of their own records
    # (event_log:<n>), the keys they were emitted with (event_log_key:<hash>), where each consumer's next look starts
    # (event_cursor:<consumer>) and what each consumer claimed (event_claim:<hash of consumer and key>)
    "DEFINE TABLE IF NOT EXISTS event_log SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_log_logged ON event_log FIELDS logged_at",
    "DEFINE INDEX IF NOT EXISTS event_log_published ON event_log FIELDS published",
    "DEFINE TABLE IF NOT EXISTS event_log_key SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_log_key_at ON event_log_key FIELDS at",
    "DEFINE TABLE IF NOT EXISTS event_cursor SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS event_claim SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_claim_at ON event_claim FIELDS at",
    # what automations keep between activities (domain/match.py): the last value a `changed` test saw, joins waiting
    # for their other triggers and decide answers (automation_state:<hash>), and how many went to make room
    "DEFINE TABLE IF NOT EXISTS automation_state SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS automation_state_automation ON automation_state FIELDS automation, at",
    "DEFINE INDEX IF NOT EXISTS automation_state_expires ON automation_state FIELDS expires_at",
    "DEFINE TABLE IF NOT EXISTS automation_state_dropped SCHEMALESS",
    # an automation trigger's webhook (domain/automation_hooks.py): automation_hook:<n>, the token kept only as a hash
    "DEFINE TABLE IF NOT EXISTS automation_hook SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS automation_hook_token ON automation_hook FIELDS token_hash UNIQUE",
    "DEFINE INDEX IF NOT EXISTS automation_hook_automation ON automation_hook FIELDS automation",
    # timers (domain/timers.py, docs/automations.md, Time): timer:<hash of key>, what runs when, for which automation
    "DEFINE TABLE IF NOT EXISTS timer SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS timer_due ON timer FIELDS status, at",
    "DEFINE INDEX IF NOT EXISTS timer_automation ON timer FIELDS automation, status",
    "DEFINE INDEX IF NOT EXISTS timer_done ON timer FIELDS done_at",
    # automations (domain/automations.py, docs/automations.md): automation:<n>
    "DEFINE TABLE IF NOT EXISTS automation SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS automation_enabled ON automation FIELDS enabled",
    # its runs (automation_run:<n>), what their action nodes meant to do (automation_intent:<run>-<node>) and the
    # counters its limits are kept with (automation_limit:<automation or all>-<hour or minute>)
    "DEFINE TABLE IF NOT EXISTS automation_run SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS automation_run_a ON automation_run FIELDS automation",
    "DEFINE INDEX IF NOT EXISTS automation_run_finished ON automation_run FIELDS finished_at",
    "DEFINE TABLE IF NOT EXISTS automation_intent SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS automation_intent_run ON automation_intent FIELDS run",
    "DEFINE INDEX IF NOT EXISTS automation_intent_a ON automation_intent FIELDS automation, status",
    "DEFINE TABLE IF NOT EXISTS automation_limit SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS upload_recording ON upload FIELDS recording",
    # automations' saved variables (domain/variables.py, docs/automations.md, Saved variables): an automation's own or
    # its namespace's, automation_var:<hash of whose and name> (the id keeps the name unique)
    "DEFINE TABLE IF NOT EXISTS automation_var SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS automation_var_automation ON automation_var FIELDS automation",
    "DEFINE INDEX IF NOT EXISTS automation_var_space ON automation_var FIELDS space",
    # review marks on the timeline (domain/event_marks.py, docs/timeline.md): each person's reviewed, important and
    # hidden flags on an activity, event_mark:<hash of account and activity> (the id keeps the pair unique)
    "DEFINE TABLE IF NOT EXISTS event_mark SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_mark_account ON event_mark FIELDS account",
    # reactions on the timeline (domain/event_reactions.py): the emojis each person gave an activity, one row per pair
    # (event_reaction:<hash of account and activity>), seen by everyone who sees the activity
    "DEFINE TABLE IF NOT EXISTS event_reaction SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_reaction_activity ON event_reaction FIELDS activity",
    "DEFINE INDEX IF NOT EXISTS event_reaction_account ON event_reaction FIELDS account",
    # mutes on the timeline (domain/event_mutes.py): each person's muted actors and kinds, for good or until a time,
    # event_mute:<hash of account and target> (the id keeps the pair unique)
    "DEFINE TABLE IF NOT EXISTS event_mute SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_mute_account ON event_mute FIELDS account",
    # opens on the timeline (domain/for_you.py): how often each person opened an activity, for their For you order
    # alone, event_open:<hash of account and activity>
    "DEFINE TABLE IF NOT EXISTS event_open SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS event_open_account ON event_open FIELDS account",
    # every line of a run's log, in chunks (jobs.RunLog): job_log:<random>
    "DEFINE TABLE IF NOT EXISTS job_log SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_log_job ON job_log FIELDS job",
    # the last few times each kind of step took, for estimates (jobs.estimates): step_stat:<type> and :<type:template>
    "DEFINE TABLE IF NOT EXISTS step_stat SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS worker SCHEMALESS",
    # storage
    "DEFINE TABLE IF NOT EXISTS storage_source SCHEMALESS",
    # a sign-in through rclone on this server, waiting for the browser to come back (domain/rclone_signin.py):
    # rclone_signin:<random>, one at a time, for ten minutes
    "DEFINE TABLE IF NOT EXISTS rclone_signin SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS watch_path SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS watch_path_source ON watch_path FIELDS source",
    "DEFINE TABLE IF NOT EXISTS remote_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS remote_file_watch ON remote_file FIELDS watch",
    # sensors (sensors.py): stream sensors are storage_source rows too, found by their key (mqtt:<prefix>,
    # syslog:<address>, webhook:<id>) or a webhook's token hash; their streams (sensor_stream:<sensor>-<hash>), readings,
    # hourly rollups (sensor_rollup:<stream>-<field>-<hour>), hub logins and the processes running the hub
    "DEFINE INDEX IF NOT EXISTS storage_source_key ON storage_source FIELDS key",
    "DEFINE INDEX IF NOT EXISTS storage_source_push ON storage_source FIELDS push_hash",
    # sensors discovery made, newest first, as sensor.joined (events_streams.py)
    "DEFINE INDEX IF NOT EXISTS storage_source_created ON storage_source FIELDS created_at",
    "DEFINE TABLE IF NOT EXISTS sensor_stream SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_stream_sensor ON sensor_stream FIELDS sensor",
    "DEFINE TABLE IF NOT EXISTS sensor_reading SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_reading_sensor ON sensor_reading FIELDS sensor, at",
    "DEFINE INDEX IF NOT EXISTS sensor_reading_stream ON sensor_reading FIELDS stream, at",
    "DEFINE TABLE IF NOT EXISTS sensor_rollup SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_rollup_stream ON sensor_rollup FIELDS stream, field, hour",
    "DEFINE INDEX IF NOT EXISTS sensor_rollup_sensor ON sensor_rollup FIELDS sensor, hour",
    # a log stream's patterns (sensor_pattern:<stream>-<hash of the template>): counts, a label (routine, notable,
    # alert: by the decision model or a person) and an action (drop: counted, not kept)
    "DEFINE TABLE IF NOT EXISTS sensor_pattern SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_sensor ON sensor_pattern FIELDS sensor, stream",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_stream ON sensor_pattern FIELDS stream",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_action ON sensor_pattern FIELDS action",
    "DEFINE INDEX IF NOT EXISTS sensor_pattern_label ON sensor_pattern FIELDS label",
    "DEFINE INDEX IF NOT EXISTS sensor_reading_pattern ON sensor_reading FIELDS pattern",
    "DEFINE TABLE IF NOT EXISTS sensor_login SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS sensor_login_name ON sensor_login FIELDS username UNIQUE",
    "DEFINE TABLE IF NOT EXISTS sensor_service SCHEMALESS",
    # the file-sharing servers running now (smb_share.py): one row per server and process, refreshed while it runs
    "DEFINE TABLE IF NOT EXISTS share_service SCHEMALESS",
    # the chat-room bridge (bridge.py): which process reads Matterbridge (bridge_state:lease); its conversations are
    # chats with a `bridge` key (the room and person)
    "DEFINE TABLE IF NOT EXISTS bridge_state SCHEMALESS",
    # where each chat channel tells its account's person things nobody asked about yet (chat_route:<channel>: the
    # private chat it last had with someone it vouches for; channels.tell)
    "DEFINE TABLE IF NOT EXISTS chat_route SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS chat_route_account ON chat_route FIELDS account",
    # templates, pipelines, outputs, chat, edits
    "DEFINE TABLE IF NOT EXISTS template SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS template_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS template_version_t ON template_version FIELDS template",
    "DEFINE TABLE IF NOT EXISTS pipeline SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS pipeline_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS pipeline_version_p ON pipeline_version FIELDS pipeline",
    "DEFINE TABLE IF NOT EXISTS workflow SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS content_type SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS workflow_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS workflow_version_w ON workflow_version FIELDS workflow",
    # custom nodes: bodies of nodes saved under a name, used in workflows (custom_nodes.py)
    "DEFINE TABLE IF NOT EXISTS custom_node SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS custom_node_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS custom_node_version_n ON custom_node_version FIELDS node",
    # extensions: tools, skills, hooks and plugins added to the assistant (extensions.py)
    "DEFINE TABLE IF NOT EXISTS extension SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS extension_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS extension_version_e ON extension_version FIELDS extension",
    "DEFINE TABLE IF NOT EXISTS marketplace SCHEMALESS",  # where plugins and skills are installed from (marketplace.py)
    # routines (scheduled syncs, pipelines and workflows) and the graph changes they make or propose
    "DEFINE TABLE IF NOT EXISTS seed SCHEMALESS",  # what has been seeded once: seed:routines
    "DEFINE TABLE IF NOT EXISTS routine SCHEMALESS",
    # Fedora (fedora.py): what to send, what was sent (a hash per resource path) and how the last sync went
    "DEFINE TABLE IF NOT EXISTS fedora_outbox SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS fedora_state SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS fedora_status SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS routine_run SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS routine_run_r ON routine_run FIELDS routine",
    # finished runs, by when, as routine.ran (events_streams.py)
    "DEFINE INDEX IF NOT EXISTS routine_run_finished ON routine_run FIELDS finished_at",
    "DEFINE TABLE IF NOT EXISTS graph_change SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS graph_change_run ON graph_change FIELDS run",
    "DEFINE INDEX IF NOT EXISTS graph_change_status ON graph_change FIELDS status",
    "DEFINE INDEX IF NOT EXISTS graph_change_pair ON graph_change FIELDS pair",
    # the entity graph's history (graph_history.py): one append-only event per change, and named versions
    "DEFINE TABLE IF NOT EXISTS graph_event SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS graph_event_at ON graph_event FIELDS at",  # read as activities (events_content.py)
    "DEFINE TABLE IF NOT EXISTS graph_tag SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS graph_checkpoint SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS graph_checkpoint_part SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS graph_checkpoint_part_cp ON graph_checkpoint_part FIELDS checkpoint",
    # what a podcast episode was made from and of, beside its recording (app/domain/podcasts.py): podcast:<recording id>
    "DEFINE TABLE IF NOT EXISTS podcast SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS podcast_space ON podcast FIELDS space",
    # published RSS and podcast feeds (app/domain/syndication.py): feed:<n>, its token kept sealed and as a hash, and
    # its items, one per feed for what each is about (key: "<feed>:<guid>")
    "DEFINE TABLE IF NOT EXISTS feed SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS feed_token ON feed FIELDS token_hash UNIQUE",
    "DEFINE INDEX IF NOT EXISTS feed_space ON feed FIELDS space",
    "DEFINE TABLE IF NOT EXISTS feed_item SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS feed_item_feed ON feed_item FIELDS feed",
    "DEFINE INDEX IF NOT EXISTS feed_item_key ON feed_item FIELDS key UNIQUE",
    # circles of trust and what people share with them (app/domain/circles.py): circle:<n> (owner, name, members),
    # circle_share:<n> (a snapshot of what was shared), circle_share_to:<n> (one per member it went to)
    "DEFINE TABLE IF NOT EXISTS circle SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS circle_owner ON circle FIELDS owner",
    "DEFINE TABLE IF NOT EXISTS circle_share SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS circle_share_by ON circle_share FIELDS by",
    "DEFINE INDEX IF NOT EXISTS circle_share_at ON circle_share FIELDS at",
    "DEFINE TABLE IF NOT EXISTS circle_share_to SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS circle_share_to_account ON circle_share_to FIELDS account",
    "DEFINE INDEX IF NOT EXISTS circle_share_to_share ON circle_share_to FIELDS share",
    "DEFINE INDEX IF NOT EXISTS circle_share_to_at ON circle_share_to FIELDS at",
    # a reply drafted to an email (app/domain/responder.py): email_reply:<recording id>
    "DEFINE TABLE IF NOT EXISTS email_reply SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS email_reply_space ON email_reply FIELDS space",
    # what someone's agent asked of another's (app/domain/circles_ask.py): agent_ask:<n> (by, to, what, start, end, at)
    "DEFINE TABLE IF NOT EXISTS agent_ask SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS agent_ask_to ON agent_ask FIELDS to",
    "DEFINE INDEX IF NOT EXISTS agent_ask_at ON agent_ask FIELDS at",
    "DEFINE TABLE IF NOT EXISTS output SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS output_rec ON output FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS chat SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS chat_account ON chat FIELDS account",
    "DEFINE TABLE IF NOT EXISTS chat_message SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS chat_message_chat ON chat_message FIELDS chat",
    # what people said to Lens, by time, as chat.message (events_streams.py)
    "DEFINE INDEX IF NOT EXISTS chat_message_said ON chat_message FIELDS role, created_at",
    # what each namespace's assistant remembers (ns_assistant.py); its name and instructions are space.assistant
    "DEFINE TABLE IF NOT EXISTS assistant_memory SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS assistant_memory_space ON assistant_memory FIELDS space",
    # projects (projects.py): someone's goal, the namespaces it draws on, and its conversations (chat.project)
    "DEFINE TABLE IF NOT EXISTS project SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS project_account ON project FIELDS account",
    "DEFINE INDEX IF NOT EXISTS chat_project ON chat FIELDS project",
    # browsers connected for the assistant to use (browser.py): a side panel's session, and the actions asked of it
    "DEFINE TABLE IF NOT EXISTS browser_session SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS browser_session_account ON browser_session FIELDS account",
    "DEFINE TABLE IF NOT EXISTS browser_action SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS browser_action_session ON browser_action FIELDS session, state",
    # each conversation's code sandbox on the OpenShell gateway (sandbox.py): its name, settings and last use
    "DEFINE TABLE IF NOT EXISTS code_sandbox SCHEMALESS",
    # deep research (research.py): a goal's plan, its run's progress and report; research_run:<id> is the claim of
    # the process carrying a run out, with its heartbeat
    "DEFINE TABLE IF NOT EXISTS research SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS research_account ON research FIELDS account, status",
    "DEFINE INDEX IF NOT EXISTS research_chat ON research FIELDS chat",
    "DEFINE TABLE IF NOT EXISTS research_run SCHEMALESS",
    # files a conversation's sandbox handed the person to download (sandbox.share), kept with the conversation
    "DEFINE TABLE IF NOT EXISTS chat_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS chat_file_chat ON chat_file FIELDS chat",
    "DEFINE TABLE IF NOT EXISTS segment_edit SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS segment_edit_rec ON segment_edit FIELDS recording",
    # descriptive metadata history, IIIF change discovery and IIIF authorization
    "DEFINE TABLE IF NOT EXISTS meta_edit SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS meta_edit_target ON meta_edit FIELDS target",
    "DEFINE TABLE IF NOT EXISTS iiif_activity SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS iiif_cookie SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS iiif_token SCHEMALESS",
    # entity curation: aliases from merges, per-mention overrides, not-the-same pairs, merge snapshots, cross-namespace links
    "DEFINE TABLE IF NOT EXISTS entity_alias SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_alias_space ON entity_alias FIELDS space",
    "DEFINE INDEX IF NOT EXISTS entity_alias_entity ON entity_alias FIELDS entity",
    "DEFINE TABLE IF NOT EXISTS entity_override SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS entity_override_rec ON entity_override FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS entity_distinct SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS entity_merge SCHEMALESS",
    "DEFINE TABLE IF NOT EXISTS entity_link SCHEMALESS",
    # video: shots, text on screen, faces
    "DEFINE TABLE IF NOT EXISTS shot SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS shot_rec ON shot FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS ocr_span SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS ocr_span_rec ON ocr_span FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS ocr_span_space ON ocr_span FIELDS space",
    "DEFINE TABLE IF NOT EXISTS face SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS face_space ON face FIELDS space",
    "DEFINE INDEX IF NOT EXISTS face_label ON face FIELDS label_key UNIQUE",
    "DEFINE TABLE IF NOT EXISTS face_track SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS face_track_rec ON face_track FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS face_track_face ON face_track FIELDS face",
    "DEFINE TABLE IF NOT EXISTS face_suggestion SCHEMALESS",
    # objects: one row per kind of object on a recording (app/domain/objects.py)
    "DEFINE TABLE IF NOT EXISTS object_track SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS object_track_rec ON object_track FIELDS recording",
    # what a model that can see images says a page or a shot shows (descriptions.py)
    "DEFINE TABLE IF NOT EXISTS description SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS description_rec ON description FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS face_merge SCHEMALESS",
    # passages embedded for search by meaning (app/domain/semantic.py), and which model's vectors they hold; their
    # HNSW index is defined when the first vector is stored, since its dimension is the model's
    "DEFINE TABLE IF NOT EXISTS passage SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS passage_rec ON passage FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS passage_space ON passage FIELDS space",
    "DEFINE TABLE IF NOT EXISTS embedding_state SCHEMALESS",
    # each image resource's CLIP vector, for searching photos by what they show (app/domain/photos.py): photo_vector:<rid>
    "DEFINE TABLE IF NOT EXISTS photo_vector SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS photo_vector_space ON photo_vector FIELDS space",
    # collections, batch runs, assistant approvals
    "DEFINE TABLE IF NOT EXISTS saved_collection SCHEMALESS",
    # saved views of the Library (app/domain/views.py)
    "DEFINE TABLE IF NOT EXISTS saved_view SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS saved_view_account ON saved_view FIELDS account",
    "DEFINE INDEX IF NOT EXISTS saved_view_space ON saved_view FIELDS space",
    # notes on recordings, yours or shared with everyone who can read it (app/domain/notes.py)
    "DEFINE TABLE IF NOT EXISTS note SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_rec ON note FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS note_account ON note FIELDS account",
    # notes as pages: free notes in a tree and a page per resource, entity or topic (app/domain/notebook.py);
    # about_key is "<space>:<kind>:<id>" for a thing's own page
    "DEFINE TABLE IF NOT EXISTS note_page SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_page_space ON note_page FIELDS space",
    "DEFINE INDEX IF NOT EXISTS note_page_parent ON note_page FIELDS parent",
    "DEFINE INDEX IF NOT EXISTS note_page_about ON note_page FIELDS about_key UNIQUE",
    "DEFINE INDEX IF NOT EXISTS note_page_refine ON note_page FIELDS refine_pending",
    "DEFINE INDEX IF NOT EXISTS note_page_created ON note_page FIELDS created_at",
    # the links in a page's body (@ and # mentions), for backlinks and the graph
    "DEFINE TABLE IF NOT EXISTS note_link SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_link_page ON note_link FIELDS page",
    "DEFINE INDEX IF NOT EXISTS note_link_target ON note_link FIELDS target",
    # what a page was before each change (notebook.snapshot): its history, to look back at and restore
    "DEFINE TABLE IF NOT EXISTS note_version SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_version_page ON note_version FIELDS page",
    "DEFINE INDEX IF NOT EXISTS note_version_at ON note_version FIELDS at",
    # deleted pages, under their own id, until they're put back or TRASH_DAYS pass (notebook.delete)
    "DEFINE TABLE IF NOT EXISTS note_trash SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_trash_space ON note_trash FIELDS space",
    # the files on a note page (images, attachments), kept where Settings → Storage says (notebook.py, blobs.py)
    "DEFINE TABLE IF NOT EXISTS note_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS note_file_page ON note_file FIELDS page, key",
    # comments on resources, and on activities of the timeline (`activity`), threaded, by everyone who can read them
    # (app/domain/comments.py)
    "DEFINE TABLE IF NOT EXISTS comment SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS comment_rec ON comment FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS comment_activity ON comment FIELDS activity",
    "DEFINE INDEX IF NOT EXISTS comment_parent ON comment FIELDS parent",
    "DEFINE INDEX IF NOT EXISTS comment_created ON comment FIELDS created_at",
    # passages of resources marked in colour by their editors (app/domain/highlights.py)
    "DEFINE TABLE IF NOT EXISTS highlight SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS highlight_rec ON highlight FIELDS recording",
    # custom metadata fields defined on namespaces and collections (app/domain/fields.py); key: "<space>:<collection>:<target>:<name>"
    "DEFINE TABLE IF NOT EXISTS field SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS field_space ON field FIELDS space",
    "DEFINE INDEX IF NOT EXISTS field_collection ON field FIELDS collection",
    "DEFINE INDEX IF NOT EXISTS field_key ON field FIELDS key UNIQUE",
    # a resource's supplementary files, and the lines parsed from its transcripts, captions and indexes (app/domain/files.py)
    "DEFINE TABLE IF NOT EXISTS resource_file SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS resource_file_rec ON resource_file FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS resource_file_space ON resource_file FIELDS space",
    # the pages of documents and images, drawn and read (app/domain/documents.py): page:⟨<resource>-<index>⟩
    "DEFINE TABLE IF NOT EXISTS page SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS page_rec ON page FIELDS recording",
    "DEFINE TABLE IF NOT EXISTS file_line SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS file_line_file ON file_line FIELDS file",
    "DEFINE INDEX IF NOT EXISTS file_line_rec ON file_line FIELDS recording",
    "DEFINE INDEX IF NOT EXISTS file_line_space ON file_line FIELDS space",
    "DEFINE INDEX IF NOT EXISTS saved_collection_owner ON saved_collection FIELDS account",
    "DEFINE TABLE IF NOT EXISTS batch SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS job_batch ON job FIELDS batch",
    "DEFINE TABLE IF NOT EXISTS approval SCHEMALESS",
    "DEFINE INDEX IF NOT EXISTS approval_chat ON approval FIELDS chat",
    # the inbox (approvals.py): a person's waiting approvals, an automation's (capped), and the expiry sweep
    "DEFINE INDEX IF NOT EXISTS approval_waiting ON approval FIELDS account, status",
    "DEFINE INDEX IF NOT EXISTS approval_automation ON approval FIELDS origin.automation",
    "DEFINE INDEX IF NOT EXISTS approval_expires ON approval FIELDS expires_at",
    # approvals as activities (events_approvals.py): asked by created_at, decided by decided_at
    "DEFINE INDEX IF NOT EXISTS approval_created ON approval FIELDS created_at",
    "DEFINE INDEX IF NOT EXISTS approval_decided ON approval FIELDS decided_at",
    # the scheduled backup (domain/backups.py): backup_schedule:next holds when it's next due and how the last went
    "DEFINE TABLE IF NOT EXISTS backup_schedule SCHEMALESS",
    # backup_remote:last: how copying the last backup to backups.connection went
    "DEFINE TABLE IF NOT EXISTS backup_remote SCHEMALESS",
]


def _analyzer(cfg):
    stem = ", snowball(english)" if cfg["search"]["stemming"] == "english" else ""
    return f"DEFINE ANALYZER IF NOT EXISTS archive_text TOKENIZERS class FILTERS lowercase, ascii{stem}"


# the full-text indexes, each on the `text` of its table: transcripts, text on screen, and lines of supplementary files
TEXT_INDEXES = (
    ("segment_text", "segment"),
    ("ocr_text", "ocr_span"),
    ("file_text", "file_line"),
    ("object_text", "object_track"),
    ("description_text", "description"),
)


SEARCH_ENGINES = ("sqlite", "surrealdb", "opensearch")


def _text_index(db):
    # SurrealDB 3 spells full-text indexes FULLTEXT; 2.x (and the embedded engine) spell them SEARCH.
    found = None
    for kw in ("FULLTEXT", "SEARCH"):
        try:
            for index, table in TEXT_INDEXES:
                db.q(f"DEFINE INDEX IF NOT EXISTS {index} ON {table} FIELDS text {kw} ANALYZER archive_text BM25 HIGHLIGHTS")
            return kw
        except Exception:  # noqa: BLE001
            continue
    return found


def search_settings(db, cfg):
    """search.*, with what was saved in the app over archive.yaml: the engine has to be known before the settings are."""
    from . import settings

    return settings.effective(db, cfg)["search"]


def _search_engine(db, cfg, rebuild=False):
    """Set up word search for search.engine: the words in a SQLite file or an OpenSearch cluster, kept in step by
    events (textindex.py), or SurrealDB's own index. SurrealDB's is taken down when it isn't used, so its memory and
    disk are given back."""
    from . import textindex

    search = search_settings(db, cfg)
    old = getattr(db, "textindex", None)
    if old is not None:
        old.close()
        db.textindex = None
    index = textindex.open_index(db, {**cfg, "search": search})
    if index is None:
        db.run(
            [f"REMOVE EVENT IF EXISTS {table}_textindex ON {table}" for _, table in TEXT_INDEXES] + ["DELETE text_change"],
            transaction=False,
        )
        db.fulltext = _text_index(db)
        return
    db.run(
        [f"REMOVE INDEX IF EXISTS {name} ON {table}" for name, table in TEXT_INDEXES]
        + ["DEFINE TABLE IF NOT EXISTS text_change SCHEMALESS"]
        + [textindex.event(table) for _, table in TEXT_INDEXES],
        transaction=False,
    )
    db.fulltext = None
    db.textindex = index
    if rebuild:
        db.textindex.rebuild()


def connect(cfg, upgrade=True):
    """Open the database, apply the schema and, unless upgrade=False (for `lens migrations`), bring its data up to
    date (domain/migrations.py)."""
    db = DB(cfg)
    prepare(db, cfg)
    if upgrade:
        migrate(db)
    return db


def prepare(db, cfg):
    """The schema, word search and the namespaces archive.yaml names: on every start, and after a restore
    (domain/backups.py)."""
    # one round trip, not one per statement: against a server that was over a second for every test's database
    db.run([_analyzer(cfg), *SCHEMA], transaction=False)
    _search_engine(db, cfg, rebuild=getattr(db, "textindex", None) is not None)
    for name, spec in cfg["namespaces"].items():
        sid = ns_id(db, name)
        db.q("UPDATE $r SET graph = $g", r=R("space", sid), g=spec["graph"])


def migrate(db):
    """Run the data upgrades this database hasn't had yet (domain/migrations.py)."""
    from . import migrations

    return migrations.run(db)


def reindex(db, cfg):
    """Rebuild the full-text index for the search settings saved now (search.engine and search.stemming)."""
    if search_settings(db, cfg)["engine"] == "surrealdb":
        for s in [f"REMOVE INDEX IF EXISTS {index} ON {table}" for index, table in TEXT_INDEXES] + [
            "REMOVE ANALYZER IF EXISTS archive_text"
        ]:
            try:
                db.q(s)
            except Exception:  # noqa: BLE001
                pass
        db.q(_analyzer({**cfg, "search": search_settings(db, cfg)}))
        _search_engine(db, cfg)
        db._text_ready = True  # just built from the current data
    else:
        _search_engine(db, cfg, rebuild=True)


def ns_id(db, name, create=True):
    row = db.one("SELECT record::id(id) AS id FROM space WHERE name = $n LIMIT 1", n=name)
    if row:
        return row["id"]
    if not create:
        raise KeyError(name)
    if not NS_RX.match(name or ""):
        raise SystemExit(f"namespace names use lowercase letters, digits, - and _: {name!r}")
    sid = db.next_id("space")
    try:
        db.q("CREATE $r CONTENT $d", r=R("space", sid), d={"name": name, "graph": "shared"})
    except Exception:  # noqa: BLE001 - another process made it first (the API and a worker starting on a fresh database)
        row = db.one("SELECT record::id(id) AS id FROM space WHERE name = $n LIMIT 1", n=name)
        if not row:
            raise
        return row["id"]
    default_collection(db, sid)
    return sid


DEFAULT_COLLECTION = "General"


def collection_key(sid, parent, name):
    """What makes a collection's name unique: its namespace, its parent and the name, ignoring case."""
    return f"{sid}:{parent or 0}:{' '.join(str(name).split()).casefold()}"


def default_collection(db, sid):
    """The namespace's default collection, where recordings nobody placed go: made ("General") when it has none."""
    row = db.one("SELECT default_collection FROM $r", r=R("space", sid)) or {}
    cid = row.get("default_collection")
    if cid is not None and db.one("SELECT record::id(id) AS id FROM $r", r=R("collection", cid)):
        return cid
    key = collection_key(sid, None, DEFAULT_COLLECTION)
    found = db.one("SELECT record::id(id) AS id FROM collection WHERE key = $k LIMIT 1", k=key)
    if found:
        cid = found["id"]
    else:
        cid = db.next_id("collection")
        try:
            db.q(
                "CREATE $r CONTENT $d",
                r=R("collection", cid),
                d={"space": sid, "name": DEFAULT_COLLECTION, "key": key, "created_at": now()},
            )
        except Exception:  # noqa: BLE001 - another process made it first
            cid = db.one("SELECT record::id(id) AS id FROM collection WHERE key = $k LIMIT 1", k=key)["id"]
    db.q("UPDATE $r SET default_collection = $c", r=R("space", sid), c=cid)
    return cid


def home(db, sid, collection=None):
    """The collection a new recording in namespace `sid` goes into: `collection` (KeyError unless it's one of the
    namespace's), else the namespace's default."""
    if collection is None:
        return default_collection(db, sid)
    row = db.one("SELECT space FROM $r", r=R("collection", int(collection)))
    if not row or row["space"] != sid:
        raise KeyError(collection)
    return int(collection)


def space_names(db):
    return {r["id"]: r["name"] for r in db.rows("SELECT record::id(id) AS id, name FROM space")}


# Segments past $keep are deleted; callers overwrite the ones they keep. Never delete and re-create the same record id in one
# transaction: SurrealDB 3.2 silently drops records re-created that way (and INSERT fails with "already exists").
DOWNSTREAM = [
    "DELETE mentions WHERE recording = $rid",
    "DELETE term WHERE recording = $rid",
    "DELETE section WHERE recording = $rid",
    "DELETE date_said WHERE recording = $rid",
    "DELETE appearance WHERE recording = $rid",
    "DELETE segment WHERE recording = $rid AND idx >= $keep",
    "DELETE passage WHERE recording = $rid",
]


def reset_downstream(db, rid):
    """Remove everything derived from a recording's transcript."""
    db.run(DOWNSTREAM + ["UPDATE $rec SET embedded = NONE"], rid=rid, keep=0, rec=R("recording", rid))


def clean(d):
    return {k: v for k, v in d.items() if v is not None}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def tc(ms):
    s = max(0, int((ms or 0) // 1000))
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


class Busy(RuntimeError):
    pass


@contextlib.contextmanager
def lock(cfg, name):
    """One batch run at a time on this machine; a second run of the same step exits instead of doubling up."""
    d = pathlib.Path(cfg["data_dir"]) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"{name}.lock", "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(name) from None
        yield
    finally:
        f.close()
