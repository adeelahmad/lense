/**
 * Settings saved in the app (Settings ST1–ST3, IIIF & metadata MD5): the backend's sections and fields, how each field
 * is shown and parsed, validation that mirrors the backend (app/domain/settings.py), and the list of changes shown in
 * the review before a new public base URL is saved.
 */
import { RIGHTS_RX, rightsShort } from "@/components/iiif/rights";

export type SectionValues = Record<string, unknown>;
export type SectionView = {
  values: SectionValues;
  updated_at?: string | null;
  updated_by?: string | null;
  locked?: string[];
};
export type SettingsView = Record<string, SectionView> & {
  bootstrap?: {
    database?: string;
    data_dir?: string;
    secret_key?: string;
    rclone?: string;
    local_roots?: string[];
    soffice?: string;
    chromium?: string;
    /** Networks web pages may be captured from besides the public internet (documents.web_networks). */
    web_networks?: string[];
    /** The YOLOX model the objects step uses, or "not found". */
    yolox_model?: string;
  } & Record<string, unknown>;
};

export type Kind =
  "text" | "number" | "int" | "select" | "switch" | "lines" | "checks" | "secret" | "pills" | "cards" | "days";
export type Opt = { value: string; label: string; hint?: string };

export type FieldSpec = {
  /** Backend section, e.g. "server". */
  section: string;
  /** Key in the section; nested keys use a dot: "mlx_whisper.model". */
  key: string;
  label: string;
  kind: Kind;
  options?: Opt[];
  hint?: string;
  min?: number;
  max?: number;
  /** Empty means null (use the built-in behaviour). */
  nullable?: boolean;
  mono?: boolean;
  placeholder?: string;
  unit?: string;
};

export type SectionId =
  | "transcription"
  | "speaker-separation"
  | "speech-providers"
  | "voice-ids"
  | "analysis"
  | "llm"
  | "local-model"
  | "ai"
  | "search"
  | "reports"
  | "video"
  | "workers"
  | "components"
  | "access"
  | "remote-access"
  | "sign-in"
  | "notifications"
  | "mail"
  | "bridge"
  | "telemetry"
  | "fedora"
  | "sensors"
  | "uploads"
  | "documents"
  | "tokens"
  | "iiif"
  | "startup";

/** Sections every member of a namespace has, kept apart from the archive's settings (which are for admins). */
export type WorkspaceSectionId = "speakers";
export type AnySectionId = SectionId | WorkspaceSectionId;

export const WORKSPACE_SECTIONS: { id: WorkspaceSectionId; label: string }[] = [{ id: "speakers", label: "Speakers" }];

export function isWorkspaceSection(id: string): id is WorkspaceSectionId {
  return WORKSPACE_SECTIONS.some((s) => s.id === id);
}

export type SectionSpec = {
  id: SectionId;
  label: string;
  backend: string[];
  description: string;
};

export const SECTIONS: SectionSpec[] = [
  {
    id: "transcription",
    label: "Transcription",
    backend: ["transcribe"],
    description: "Which engine turns speech into text, where it runs, and in which language.",
  },
  {
    id: "speaker-separation",
    label: "Speaker separation",
    backend: ["diarize"],
    description: "How a recording is split into speakers before voices are matched.",
  },
  {
    id: "speech-providers",
    label: "Speech providers",
    backend: ["speech"],
    description:
      "Services that can transcribe, tell speakers apart and read answers aloud instead of this server. Each takes its own address, for a proxy or a compatible server.",
  },
  {
    id: "voice-ids",
    label: "Voice IDs",
    backend: ["speakers"],
    description:
      "Each voice in a recording is compared with the speakers in its namespace. The score decides what happens next.",
  },
  {
    id: "analysis",
    label: "Analysis",
    backend: ["analysis"],
    description: "How people, organisations, products, places and topics are found in transcripts.",
  },
  {
    id: "llm",
    label: "LLM provider",
    backend: ["llm"],
    description: "The OpenAI-compatible model used for summaries, templates and chat.",
  },
  {
    id: "local-model",
    label: "Local model",
    backend: ["local_llm"],
    description:
      "Run a chat model on this machine with llama.cpp: pick one that fits, and Lens downloads it from Hugging Face and serves it as the LLM provider.",
  },
  {
    id: "ai",
    label: "AI assistant",
    backend: ["ai", "decisions", "voice"],
    description:
      "What the chat assistant may do with tools, which routine choices it makes for you, and when a batch run needs a typed confirmation.",
  },
  {
    id: "search",
    label: "Search",
    backend: ["search", "embeddings"],
    description: "How transcripts are indexed for search, and search by meaning with an embedding model.",
  },
  {
    id: "reports",
    label: "Reports and graph",
    backend: ["reports", "graph"],
    description: "What reports carry, and how big the knowledge graph gets.",
  },
  {
    id: "video",
    label: "Video, OCR, faces and objects",
    backend: ["video"],
    description: "Shots and keyframes, text on screen, faces, and objects in videos, documents and images.",
  },
  {
    id: "workers",
    label: "Workers",
    backend: ["workers"],
    description: "The workers inside the server, and how the job queue retries.",
  },
  {
    id: "sign-in",
    label: "Sign-in",
    backend: ["auth"],
    description:
      "How people sign in: passkeys (fingerprint, face or device PIN) always; passwords only if you allow them.",
  },
  {
    id: "components",
    label: "Components",
    backend: ["components"],
    description:
      "The engines and models Lens fetches for itself, sized to each worker’s machine. Steps that need one wait while it arrives.",
  },
  {
    id: "access",
    label: "Access & embedding",
    backend: ["server"],
    description:
      "Who can reach the server, how it tells visitors’ addresses, which sites may embed the player, and how long sessions last.",
  },
  {
    id: "remote-access",
    label: "Remote access",
    backend: ["tunnel"],
    description:
      "Off unless you turn it on. Reach Lens from anywhere at an https:// address through a Cloudflare Tunnel that Lens runs itself: nothing to open on your router.",
  },
  {
    id: "mail",
    label: "Email",
    backend: ["mail"],
    description: "The SMTP server Lens sends email through: access requests and password resets.",
  },
  {
    id: "bridge",
    label: "Chat rooms",
    backend: ["bridge"],
    description:
      "The assistant in Slack, Discord, Telegram, Matrix and other chat rooms, through Matterbridge: it answers what’s said to it there.",
  },
  {
    id: "notifications",
    label: "Notifications",
    backend: ["notifications"],
    description:
      "Where namespaces may send notifications (webhooks, Matterbridge, Slack, Discord), how often the notifier looks, and how often a failed send is tried again.",
  },
  {
    id: "telemetry",
    label: "Telemetry",
    backend: ["telemetry"],
    description:
      "Off unless you turn it on. Traces and metrics from the server and its workers, sent only to an OpenTelemetry collector you run or choose; nothing goes anywhere else.",
  },
  {
    id: "fedora",
    label: "Fedora repository",
    backend: ["fedora"],
    description:
      "Off unless you set an address. Keeps a copy of the archive in a Fedora 6 repository: every namespace, collection, recording (with its file), entity and speaker, described in RDF with Dublin Core.",
  },
  {
    id: "sensors",
    label: "Sensors",
    backend: ["sensors"],
    description:
      "Off unless you turn it on. An MQTT hub and a syslog listener in Lens’s workers, so routers, DNS servers and devices can send to it; and how long what they send is kept.",
  },
  {
    id: "uploads",
    label: "Uploads",
    backend: ["uploads"],
    description:
      "Audio and video people upload in the web app: which types, how large, and how long an unfinished upload waits.",
  },
  {
    id: "documents",
    label: "Documents",
    backend: ["documents"],
    description:
      "How documents and images are drawn and read, and how Word and other Office files, text, web pages and emails are made into PDFs to read.",
  },
  {
    id: "tokens",
    label: "API keys",
    backend: ["tokens"],
    description:
      "How long the API keys people make for scripts last, how long apps they sign in to stay signed in, and everyone’s keys, to revoke any of them.",
  },
  {
    id: "iiif",
    label: "IIIF & metadata",
    backend: ["iiif"],
    description: "How recordings are published as IIIF Manifests and Collections.",
  },
  {
    id: "startup",
    label: "Set at startup",
    backend: [],
    description:
      "These come from the server’s config file or environment and can only be changed there, followed by a restart.",
  },
];

const pct = (min = 0, max = 1) => ({ kind: "number" as const, min, max });

export const WORKER_STEPS = [
  "transcribe",
  "diarize",
  "shots",
  "ocr",
  "faces",
  "objects",
  "describe",
  "analyze",
  "embed",
  "summarize",
  "llm",
  "report",
  "export",
];

/** What uploads.extensions may name (the backend's UPLOAD_TYPES, in app/domain/settings.py). */
export const UPLOAD_TYPES = [
  ".m4a",
  ".mp3",
  ".wav",
  ".flac",
  ".ogg",
  ".opus",
  ".aac",
  ".amr",
  ".aif",
  ".aiff",
  ".wma",
  ".mp4",
  ".m4v",
  ".mov",
  ".mkv",
  ".webm",
  ".avi",
  ".mpg",
  ".mpeg",
  ".3gp",
];

export const LAYERS: Opt[] = [
  { value: "transcript", label: "Transcript lines" },
  { value: "speakers", label: "Speakers" },
  { value: "entities", label: "Entities" },
  { value: "chapters", label: "Chapters" },
  { value: "screen", label: "Text on screen" },
  { value: "faces", label: "People on screen" },
];

/** The chat assistant's tools (app/domain/ai_tools.py), with whether they change anything. */
export const AI_TOOLS: { name: string; label: string; acts: boolean }[] = [
  { name: "search_transcripts", label: "Search transcripts", acts: false },
  {
    name: "list_recordings",
    label: "List recordings (namespace, date, speaker, entity)",
    acts: false,
  },
  {
    name: "read_transcript",
    label: "Read a transcript or time range",
    acts: false,
  },
  {
    name: "recording_outputs",
    label: "Get summaries and outputs",
    acts: false,
  },
  { name: "find_entities", label: "Find entities", acts: false },
  { name: "entity_mentions", label: "Entity mentions", acts: false },
  { name: "entity_timeline", label: "Entities over time", acts: false },
  { name: "entity_setup", label: "How a namespace organises its entities", acts: false },
  {
    name: "graph_neighbours",
    label: "Explore the graph (neighbours)",
    acts: false,
  },
  { name: "speaker_stats", label: "Speakers’ talk time", acts: false },
  { name: "find_notes", label: "Find notes", acts: false },
  { name: "read_note", label: "Read a note", acts: false },
  { name: "write_note", label: "Write notes", acts: false },
  { name: "update_note", label: "Change notes", acts: false },
  { name: "run_template", label: "Run a template on recordings", acts: true },
  {
    name: "propose_entity_change",
    label: "Propose entity merges, renames, type changes, descriptions and new entities",
    acts: true,
  },
  { name: "import_files", label: "Import files sent in a conversation", acts: true },
  { name: "server_status", label: "Check what the server has set up (admins)", acts: false },
  { name: "find_model_servers", label: "Find model servers nearby (admins)", acts: false },
  { name: "read_settings", label: "Read settings (admins)", acts: false },
  { name: "change_settings", label: "Change settings (admins)", acts: true },
  { name: "create_namespace", label: "Create namespaces (admins)", acts: true },
];

/** The speech providers (the backend's app/domain/speech.py), in the order Settings shows them. */
export const SPEECH_PROVIDERS = [
  {
    id: "openai",
    label: "OpenAI-compatible",
    about:
      "OpenAI’s Whisper and GPT-4o transcription, or any server with the same /audio/transcriptions API (Groq, speaches, LocalAI). A model named …diarize also tells speakers apart.",
    urlHint: "https://api.openai.com/v1, or your proxy or compatible server",
    modelHint: "whisper-1, gpt-4o-transcribe, gpt-4o-transcribe-diarize",
  },
  {
    id: "elevenlabs",
    label: "ElevenLabs",
    about: "Scribe speech to text with speakers and sounds like laughter, and text to speech for spoken answers.",
    urlHint: "https://api.elevenlabs.io",
    modelHint: "scribe_v1",
  },
  {
    id: "assemblyai",
    label: "AssemblyAI",
    about: "Transcripts with speakers, language detection and sentiment.",
    urlHint: "https://api.assemblyai.com, or https://api.eu.assemblyai.com",
    modelHint: "universal, slam-1",
  },
  {
    id: "deepgram",
    label: "Deepgram",
    about: "Nova speech to text with speakers, language and sentiment, and Aura text to speech.",
    urlHint: "https://api.deepgram.com, or your self-hosted Deepgram",
    modelHint: "nova-3",
  },
] as const;

export const FIELDS: FieldSpec[] = [
  // Transcription
  {
    section: "transcribe",
    key: "engine",
    label: "Engine",
    kind: "select",
    options: [
      { value: "sensevoice", label: "SenseVoice" },
      { value: "whisper", label: "Whisper" },
      { value: "mlx-whisper", label: "mlx-whisper" },
      { value: "openai", label: "OpenAI-compatible (provider)" },
      { value: "elevenlabs", label: "ElevenLabs (provider)" },
      { value: "assemblyai", label: "AssemblyAI (provider)" },
      { value: "deepgram", label: "Deepgram (provider)" },
    ],
  },
  {
    section: "transcribe",
    key: "device",
    label: "Device",
    kind: "select",
    options: [
      { value: "auto", label: "Auto" },
      { value: "cpu", label: "CPU" },
      { value: "cuda", label: "CUDA" },
      { value: "mps", label: "Apple GPU" },
    ],
  },
  {
    section: "transcribe",
    key: "language",
    label: "Language",
    kind: "select",
    options: [],
  },
  {
    section: "transcribe",
    key: "sensevoice.model",
    label: "Model for SenseVoice",
    kind: "text",
    mono: true,
  },
  {
    section: "transcribe",
    key: "whisper.model",
    label: "Model for Whisper",
    kind: "text",
    mono: true,
  },
  {
    section: "transcribe",
    key: "mlx_whisper.model",
    label: "Model for mlx-whisper",
    kind: "text",
    mono: true,
  },
  // Speaker separation
  {
    section: "diarize",
    key: "engine",
    label: "Method",
    kind: "pills",
    options: [
      { value: "auto", label: "Auto" },
      { value: "channels", label: "By channel" },
      { value: "cluster", label: "Voice clustering" },
      { value: "pyannote", label: "pyannote" },
      { value: "provider", label: "Speech provider" },
      { value: "none", label: "Off" },
    ],
  },
  {
    section: "diarize",
    key: "cluster_threshold",
    label: "Clustering threshold",
    ...pct(),
    hint: "Higher splits voices more readily",
  },
  {
    section: "diarize",
    key: "min_speakers",
    label: "Min speakers",
    kind: "int",
    min: 1,
    nullable: true,
    placeholder: "auto",
  },
  {
    section: "diarize",
    key: "max_speakers",
    label: "Max speakers",
    kind: "int",
    min: 1,
    nullable: true,
    placeholder: "auto",
  },
  {
    section: "diarize",
    key: "pyannote.model",
    label: "pyannote model",
    kind: "text",
    mono: true,
  },
  {
    section: "diarize",
    key: "pyannote.token_env",
    label: "Hugging Face token variable",
    kind: "text",
    mono: true,
    hint: "The name of the environment variable holding the token",
  },
  // Voice IDs
  {
    section: "speakers",
    key: "match_threshold",
    label: "Auto-match threshold",
    ...pct(),
  },
  {
    section: "speakers",
    key: "review_threshold",
    label: "Review threshold",
    ...pct(),
  },
  {
    section: "speakers",
    key: "sample_seconds",
    label: "Seconds of audio per voiceprint",
    kind: "int",
    min: 5,
    max: 600,
    hint: "More is steadier; 20–60 s is typical",
  },
  {
    section: "speakers",
    key: "cross_namespace",
    label: "Cross-namespace suggestions",
    kind: "switch",
    hint: "Suggest “maybe the same voice” between shared namespaces. Linking never merges.",
  },
  {
    section: "speakers",
    key: "embedder",
    label: "Voice embedder",
    kind: "select",
    options: [
      { value: "speechbrain", label: "SpeechBrain" },
      { value: "none", label: "Off (no voice IDs)" },
    ],
  },
  {
    section: "speakers",
    key: "model",
    label: "Embedding model",
    kind: "text",
    mono: true,
  },
  // Analysis
  {
    section: "analysis",
    key: "entities",
    label: "Entity extraction",
    kind: "select",
    options: [
      { value: "rules", label: "Rules" },
      { value: "spacy", label: "spaCy" },
    ],
  },
  {
    section: "analysis",
    key: "spacy_model",
    label: "spaCy model",
    kind: "text",
    mono: true,
  },
  {
    section: "analysis",
    key: "gazetteer",
    label: "Custom vocabulary",
    kind: "lines",
    mono: true,
    hint: "One per line, Name | TYPE",
  },
  // LLM
  {
    section: "llm",
    key: "base_url",
    label: "Base URL (OpenAI-compatible)",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "https://…/v1",
  },
  {
    section: "llm",
    key: "model",
    label: "Model",
    kind: "text",
    mono: true,
    nullable: true,
  },
  {
    section: "llm",
    key: "chat_models",
    label: "Models people can pick in Chat",
    kind: "lines",
    mono: true,
    hint: "One per line. Empty: whatever the server lists. The model above is always offered",
  },
  {
    section: "llm",
    key: "vision_model",
    label: "Model that can see images",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "none",
    hint: "Describes each page and shot (the Describe step). Lens can't tell which models see: choose one that does",
  },
  {
    section: "llm",
    key: "describe_max",
    label: "Pages or shots described at most",
    kind: "int",
    min: 1,
    max: 1000,
    hint: "Of a resource, in order: each is a request to the model",
  },
  { section: "llm", key: "api_key", label: "API key", kind: "secret" },
  {
    section: "llm",
    key: "api_key_env",
    label: "Or read the key from this variable",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "OPENAI_API_KEY",
  },
  {
    section: "llm",
    key: "max_chars",
    label: "Max characters per request",
    kind: "int",
    min: 1000,
  },
  {
    section: "llm",
    key: "timeout",
    label: "Timeout (seconds)",
    kind: "int",
    min: 5,
    max: 3600,
  },
  // AI
  {
    section: "ai",
    key: "tools",
    label: "Let the assistant use tools",
    kind: "switch",
    hint: "Without tools, answers use search results only.",
  },
  { section: "ai", key: "disabled_tools", label: "Tools", kind: "checks" },
  {
    section: "ai",
    key: "organise_notes",
    label: "File notes in projects, areas, resources and archives",
    kind: "switch",
    hint: "Notes nobody filed are filed for you; when the assistant isn't sure, its suggestion waits on the note.",
  },
  {
    section: "ai",
    key: "refine_notes",
    label: "Keep note titles and summaries up to date",
    kind: "switch",
    hint: "A couple of minutes after a note changes, the model rewrites its one-line summary, and its title when that no longer fits.",
  },
  {
    section: "ai",
    key: "max_steps",
    label: "Tool steps per answer",
    kind: "int",
    min: 1,
    max: 50,
  },
  {
    section: "ai",
    key: "max_transcript_reads",
    label: "Transcripts read per question",
    kind: "int",
    min: 1,
    max: 500,
  },
  {
    section: "ai",
    key: "confirm_over_recordings",
    label: "Ask approval above (recordings)",
    kind: "int",
    min: 1,
    nullable: true,
    hint: "Batch runs over this many need a typed confirmation",
  },
  {
    section: "ai",
    key: "confirm_over_cost",
    label: "…or estimated cost ($)",
    kind: "number",
    min: 0,
    nullable: true,
    hint: "Needs prices per model below",
  },
  {
    section: "ai",
    key: "price_in",
    label: "Price per million input tokens ($)",
    kind: "number",
    min: 0,
    nullable: true,
  },
  {
    section: "ai",
    key: "price_out",
    label: "Price per million output tokens ($)",
    kind: "number",
    min: 0,
    nullable: true,
  },
  // Email
  {
    section: "mail",
    key: "server",
    label: "SMTP server",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "smtp.gmail.com",
  },
  { section: "mail", key: "port", label: "Port", kind: "int", min: 1, max: 65535 },
  {
    section: "mail",
    key: "security",
    label: "Connection",
    kind: "select",
    options: [
      { value: "starttls", label: "STARTTLS (usually port 587)" },
      { value: "ssl", label: "SSL/TLS (usually port 465)" },
      { value: "none", label: "Unencrypted (usually port 25)" },
    ],
  },
  { section: "mail", key: "username", label: "Username", kind: "text", nullable: true },
  { section: "mail", key: "password", label: "Password", kind: "secret" },
  {
    section: "mail",
    key: "from_address",
    label: "From address",
    kind: "text",
    nullable: true,
    placeholder: "lens@example.org",
  },
  { section: "mail", key: "from_name", label: "From name", kind: "text" },
  // Remote access (Cloudflare Tunnel)
  {
    section: "tunnel",
    key: "mode",
    label: "Tunnel",
    kind: "cards",
    options: [
      { value: "off", label: "Off", hint: "Only reachable where it runs" },
      {
        value: "quick",
        label: "Quick address",
        hint: "A random https://….trycloudflare.com address, no account needed. It changes when the tunnel restarts",
      },
      {
        value: "managed",
        label: "Your domain",
        hint: "Lens makes the tunnel and its DNS record on your Cloudflare domain, with an API token",
      },
      {
        value: "token",
        label: "Tunnel token",
        hint: "A tunnel you made in the Cloudflare dashboard (Zero Trust › Networks › Tunnels)",
      },
    ],
  },
  {
    section: "tunnel",
    key: "hostname",
    label: "Public hostname",
    kind: "text",
    mono: true,
    placeholder: "lens.example.com",
    hint: "Any name on your Cloudflare domain, like lens.example.com or archive.lens.example.com.",
  },
  { section: "tunnel", key: "token", label: "Tunnel token", kind: "secret" },
  { section: "tunnel", key: "api_token", label: "Cloudflare API token", kind: "secret" },
  {
    section: "tunnel",
    key: "origin",
    label: "Web app address for the tunnel",
    kind: "text",
    mono: true,
    placeholder: "as installed",
    hint: "Where cloudflared reaches the web app from the server. Leave empty unless you moved it.",
  },
  // Chat rooms (Matterbridge)
  { section: "bridge", key: "enabled", label: "Answer in chat rooms", kind: "switch" },
  {
    section: "bridge",
    key: "url",
    label: "Matterbridge API address",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "http://matterbridge:4242",
    hint: "The address of Matterbridge’s API account (its [api] section in matterbridge.toml).",
  },
  { section: "bridge", key: "token", label: "API token", kind: "secret" },
  {
    section: "bridge",
    key: "account",
    label: "Answers as",
    kind: "text",
    nullable: true,
    placeholder: "you, when left empty",
    hint: "The Lens account it answers as: it reads what that account can read, and changes wait for approval.",
  },
  { section: "bridge", key: "name", label: "Its name in the rooms", kind: "text" },
  {
    section: "bridge",
    key: "answer",
    label: "Answers",
    kind: "select",
    options: [
      { value: "mention", label: "Messages that name it (“Lens, …” or @Lens)" },
      { value: "all", label: "Every message" },
    ],
  },
  {
    section: "bridge",
    key: "gateway",
    label: "Only in gateway",
    kind: "text",
    nullable: true,
    placeholder: "every gateway",
  },
  {
    section: "bridge",
    key: "users",
    label: "Only for these chat usernames",
    kind: "lines",
    hint: "One per line; empty answers everyone in the bridged rooms.",
  },
  {
    section: "bridge",
    key: "poll_seconds",
    label: "Look for messages every",
    kind: "int",
    min: 1,
    max: 300,
    unit: "s",
  },
  // Components
  {
    section: "components",
    key: "auto",
    label: "Fetch what’s needed by itself",
    kind: "switch",
    hint: "Off: only report what’s missing",
  },
  {
    section: "components",
    key: "ahead",
    label: "Fetch everything now",
    kind: "switch",
    hint: "Off: engines and models are fetched the first time a recording needs them",
  },
  { section: "components", key: "also", label: "Also fetch", kind: "checks" },
  // Voice
  {
    section: "voice",
    key: "input",
    label: "What’s said is heard by",
    kind: "select",
    options: [
      { value: "auto", label: "This server when it can, else the browser" },
      { value: "server", label: "This server (stays here)" },
      { value: "browser", label: "The browser’s speech recognition" },
    ],
  },
  {
    section: "voice",
    key: "stt",
    label: "Engine that hears it",
    kind: "select",
    options: [
      { value: "same", label: "The transcription engine" },
      { value: "sensevoice", label: "SenseVoice" },
      { value: "whisper", label: "Whisper" },
      { value: "mlx-whisper", label: "mlx-whisper" },
      { value: "openai", label: "OpenAI-compatible (provider)" },
      { value: "elevenlabs", label: "ElevenLabs (provider)" },
      { value: "assemblyai", label: "AssemblyAI (provider)" },
      { value: "deepgram", label: "Deepgram (provider)" },
    ],
  },
  {
    section: "voice",
    key: "tts_provider",
    label: "Spoken answers by",
    kind: "select",
    options: [
      { value: "openai", label: "An OpenAI-compatible speech server" },
      { value: "elevenlabs", label: "ElevenLabs (provider)" },
      { value: "deepgram", label: "Deepgram Aura (provider)" },
    ],
  },
  {
    section: "voice",
    key: "tts_model",
    label: "Speech model for spoken answers",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "none: the browser reads them",
    hint: "An OpenAI-compatible /audio/speech model, e.g. kokoro or tts-1",
  },
  {
    section: "voice",
    key: "tts_voice",
    label: "Voice",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "alloy",
  },
  {
    section: "voice",
    key: "tts_base_url",
    label: "Speech server",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "the LLM provider’s",
  },
  { section: "voice", key: "tts_api_key", label: "Speech server API key", kind: "secret" },
  // Local model
  { section: "local_llm", key: "enabled", label: "Run a model here", kind: "switch" },
  {
    section: "local_llm",
    key: "model",
    label: "Model",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "pick one below",
    hint: "One from the list, or any GGUF on Hugging Face as hf:owner/repo/file.gguf",
  },
  {
    section: "local_llm",
    key: "use_as_provider",
    label: "Make it the LLM provider",
    kind: "switch",
    hint: "Off: it runs, and you point the LLM provider (or anything else) at it yourself",
  },
  { section: "local_llm", key: "context", label: "Context", kind: "int", min: 512, max: 262144, unit: "tokens" },
  {
    section: "local_llm",
    key: "threads",
    label: "CPU threads",
    kind: "int",
    min: 1,
    max: 512,
    nullable: true,
    placeholder: "auto",
  },
  {
    section: "local_llm",
    key: "gpu_layers",
    label: "Layers on the GPU",
    kind: "int",
    min: 0,
    max: 999,
    hint: "999: as many as fit; 0: CPU only",
  },
  { section: "local_llm", key: "port", label: "Port", kind: "int", min: 1024, max: 65535 },
  {
    section: "local_llm",
    key: "host",
    label: "Address others reach it at",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "automatic",
  },
  // Speech providers
  ...SPEECH_PROVIDERS.flatMap((p): FieldSpec[] => [
    {
      section: "speech",
      key: `${p.id}_base_url`,
      label: "Address",
      kind: "text",
      mono: true,
      hint: p.urlHint,
    },
    {
      section: "speech",
      key: `${p.id}_model`,
      label: "Speech-to-text model",
      kind: "text",
      mono: true,
      hint: p.modelHint,
    },
    { section: "speech", key: `${p.id}_api_key`, label: `${p.label} API key`, kind: "secret" },
  ]),
  {
    section: "speech",
    key: "sentiment",
    label: "Emotion from the provider’s sentiment",
    kind: "switch",
    hint: "AssemblyAI and Deepgram: positive reads as Happy, negative as Sad",
  },
  {
    section: "speech",
    key: "timeout",
    label: "Longest wait for a transcript",
    kind: "int",
    min: 30,
    max: 86400,
    unit: "s",
  },
  // Decisions
  {
    section: "decisions",
    key: "engine",
    label: "Who makes routine choices",
    kind: "select",
    options: [
      { value: "auto", label: "Decision model when it has a key, else the LLM" },
      { value: "jev", label: "Decision model (Jev)" },
      { value: "laya", label: "Laya, a local decision model (Apple Silicon)" },
      { value: "llm", label: "LLM provider" },
      { value: "off", label: "Nobody: always ask me" },
    ],
  },
  {
    section: "decisions",
    key: "act_above",
    label: "Act without asking from (confidence)",
    kind: "number",
    min: 0.5,
    max: 1,
    hint: "Below this the assistant asks, with its best guess first",
  },
  {
    section: "decisions",
    key: "base_url",
    label: "Decision model server",
    kind: "text",
    mono: true,
  },
  { section: "decisions", key: "model", label: "Decision model", kind: "text", mono: true },
  {
    section: "decisions",
    key: "timeout",
    label: "Timeout (seconds)",
    kind: "number",
    min: 1,
    max: 120,
  },
  { section: "decisions", key: "api_key", label: "API key", kind: "secret" },
  {
    section: "decisions",
    key: "laya_model",
    label: "Laya model",
    kind: "select",
    options: [
      { value: "aac6fef/laya-mlx", label: "Laya (English)" },
      { value: "aac6fef/laya-multilingual-mlx", label: "Laya multilingual (faster)" },
      { value: "aac6fef/laya-typed-decisions-mlx", label: "Laya typed decisions (English)" },
    ],
  },
  {
    section: "decisions",
    key: "laya_url",
    label: "Laya server",
    kind: "text",
    mono: true,
    placeholder: "http://host.docker.internal:8790/v1",
    hint: "Only when Lens runs where MLX can't, such as Docker on a Mac",
  },
  // Search
  {
    section: "search",
    key: "stemming",
    label: "Stemming",
    kind: "select",
    options: [
      { value: "english", label: "English" },
      { value: "none", label: "None" },
    ],
    hint: "Changing this needs a reindex before results change",
  },
  // Search by meaning
  {
    section: "embeddings",
    key: "enabled",
    label: "Search by meaning",
    kind: "switch",
    hint: "Finds passages about what was searched for, in other words too. Needs an embedding model",
  },
  {
    section: "embeddings",
    key: "base_url",
    label: "Embeddings server (OpenAI-compatible)",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "the LLM provider’s",
    hint: "Ollama: http://localhost:11434/v1. Empty: the LLM provider’s address and key",
  },
  {
    section: "embeddings",
    key: "model",
    label: "Embedding model",
    kind: "text",
    mono: true,
    hint: "nomic-embed-text is small and runs offline (ollama pull nomic-embed-text)",
  },
  { section: "embeddings", key: "api_key", label: "API key", kind: "secret" },
  {
    section: "embeddings",
    key: "api_key_env",
    label: "Or read the key from this variable",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "OPENAI_API_KEY",
  },
  {
    section: "embeddings",
    key: "min_similarity",
    label: "Least similarity for a match",
    kind: "number",
    min: 0,
    max: 1,
    nullable: true,
    placeholder: "what suits the model",
    hint: "0 to 1. Higher shows fewer, closer passages",
  },
  {
    section: "embeddings",
    key: "neighbours",
    label: "Passages found per search",
    kind: "int",
    min: 5,
    max: 500,
  },
  {
    section: "embeddings",
    key: "passage_chars",
    label: "Passage length",
    kind: "int",
    min: 200,
    max: 4000,
    unit: "characters",
    hint: "Lines are joined into passages this long",
  },
  {
    section: "embeddings",
    key: "batch_size",
    label: "Passages per request",
    kind: "int",
    min: 1,
    max: 256,
  },
  {
    section: "embeddings",
    key: "timeout",
    label: "Timeout (seconds)",
    kind: "int",
    min: 5,
    max: 600,
  },
  {
    section: "embeddings",
    key: "query_prefix",
    label: "Searches start with",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "what the model wants",
  },
  {
    section: "embeddings",
    key: "document_prefix",
    label: "Passages start with",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "what the model wants",
  },
  // Reports and graph
  {
    section: "reports",
    key: "audio",
    label: "Audio in reports",
    kind: "cards",
    options: [
      { value: "link", label: "Link", hint: "share link URL" },
      { value: "embed", label: "Embed", hint: "player in HTML" },
      { value: "none", label: "None", hint: "no audio" },
    ],
  },
  {
    section: "graph",
    key: "max_nodes",
    label: "Graph max nodes",
    kind: "int",
    min: 10,
    max: 5000,
  },
  {
    section: "graph",
    key: "min_edge_weight",
    label: "Minimum edge weight",
    kind: "int",
    min: 1,
    max: 1000,
  },
  // Video
  {
    section: "video",
    key: "sample_seconds",
    label: "Sample every (seconds)",
    kind: "number",
    min: 0.5,
    max: 600,
    hint: "Also one keyframe per shot",
  },
  {
    section: "video",
    key: "scene_threshold",
    label: "Shot sensitivity",
    ...pct(),
    hint: "Lower finds more shots",
  },
  {
    section: "video",
    key: "min_shot_seconds",
    label: "Shortest shot (seconds)",
    kind: "number",
    min: 0,
    max: 600,
  },
  {
    section: "video",
    key: "frame_width",
    label: "Keyframe size",
    kind: "select",
    options: [
      { value: "320", label: "320 px wide" },
      { value: "480", label: "480 px wide" },
      { value: "720", label: "720 px wide" },
      { value: "960", label: "960 px wide" },
      { value: "1280", label: "1280 px wide" },
    ],
  },
  {
    section: "video",
    key: "ocr_engine",
    label: "OCR engine",
    kind: "cards",
    options: [
      { value: "auto", label: "Auto", hint: "best available" },
      {
        value: "tesseract",
        label: "Tesseract",
        hint: "any worker · many languages",
      },
      {
        value: "apple-vision",
        label: "Apple Vision",
        hint: "Mac workers only",
      },
      { value: "rapidocr", label: "RapidOCR", hint: "CPU or CUDA · slides" },
      { value: "doctr", label: "docTR", hint: "PyTorch · scans and photos" },
      { value: "none", label: "Off", hint: "no text on screen" },
    ],
  },
  {
    section: "video",
    key: "ocr_languages",
    label: "OCR languages",
    kind: "lines",
    mono: true,
    hint: "Tesseract language codes, one per line",
  },
  {
    section: "video",
    key: "ocr_min_confidence",
    label: "Minimum confidence",
    kind: "int",
    min: 0,
    max: 100,
    hint: "Lines on frames read with less confidence are left out",
  },
  {
    section: "video",
    key: "face_engine",
    label: "Face engine",
    kind: "select",
    options: [
      { value: "opencv", label: "OpenCV (YuNet + SFace)" },
      { value: "insightface", label: "InsightFace" },
      { value: "none", label: "Off" },
    ],
  },
  {
    section: "video",
    key: "face_cluster_threshold",
    label: "Face clustering threshold",
    ...pct(),
  },
  {
    section: "video",
    key: "face_match_threshold",
    label: "Auto-match threshold",
    ...pct(),
  },
  {
    section: "video",
    key: "face_review_threshold",
    label: "Review threshold",
    ...pct(),
  },
  {
    section: "video",
    key: "publish_faces",
    label: "Publish faces in IIIF",
    kind: "switch",
    hint: "People on screen, for public video recordings only",
  },
  {
    section: "video",
    key: "object_engine",
    label: "Object detector",
    kind: "select",
    options: [
      { value: "yolox", label: "YOLOX on ONNX Runtime (Apache-2.0)" },
      { value: "ultralytics", label: "Ultralytics YOLO (AGPL-3.0)" },
      { value: "off", label: "Off" },
    ],
    hint: "Finds people, vehicles, animals and everyday things on frames and pages",
  },
  {
    section: "video",
    key: "object_min_score",
    label: "Object confidence",
    kind: "number",
    min: 0.05,
    max: 0.95,
    hint: "Objects the detector is less sure of are left out",
  },
  // Workers
  {
    section: "workers",
    key: "inline",
    label: "Workers inside the server",
    kind: "int",
    min: 0,
    max: 64,
    hint: "0 = only external workers",
  },
  {
    section: "workers",
    key: "poll_seconds",
    label: "Poll interval (seconds)",
    kind: "number",
    min: 0.2,
    max: 600,
  },
  {
    section: "workers",
    key: "stale_minutes",
    label: "Retry a silent job after (min)",
    kind: "int",
    min: 1,
    max: 1440,
  },
  {
    section: "workers",
    key: "max_attempts",
    label: "Max attempts",
    kind: "int",
    min: 1,
    max: 20,
  },
  {
    section: "workers",
    key: "steps",
    label: "Steps built-in workers run",
    kind: "checks",
    options: WORKER_STEPS.map((s) => ({ value: s, label: s })),
  },
  // Access & embedding
  {
    section: "server",
    key: "allowed_hosts",
    label: "Allowed hosts",
    kind: "lines",
    mono: true,
    hint: "One host name per line; * allows any",
  },
  {
    section: "server",
    key: "embed_frame_ancestors",
    label: "Embed origins",
    kind: "lines",
    mono: true,
    hint: "Sites that may frame the player, one per line ('self' is this site)",
  },
  {
    section: "server",
    key: "trusted_proxies",
    label: "Trusted proxies",
    kind: "lines",
    mono: true,
    hint: "The web app’s address (and other proxies in front of the server), one per line: their X-Forwarded-For names the visitor, for IP groups",
  },
  {
    section: "server",
    key: "session_hours",
    label: "Session length (days)",
    kind: "days",
    min: 1 / 24,
    max: 365,
  },
  {
    section: "server",
    key: "secure_cookies",
    label: "Secure cookies (HTTPS only)",
    kind: "switch",
  },
  {
    section: "server",
    key: "max_upload_mb",
    label: "Largest transcript file (MB)",
    kind: "int",
    min: 1,
    max: 1_000_000,
    hint: "Audio and video have their own limit, under Uploads",
  },
  // Uploads
  // Sign-in
  {
    section: "auth",
    key: "passwords",
    label: "Allow passwords",
    kind: "switch",
    hint: "Off: everyone signs in with a passkey, and password sign-in, changes and resets stop working. Turning it off needs a passkey on an admin's account first.",
  },
  // API keys
  // Sensors (opt-in)
  {
    section: "sensors",
    key: "enabled",
    label: "Run the hub",
    kind: "switch",
    hint: "Off: nothing listens for MQTT or syslog. Webhooks and bridges to other brokers work either way",
  },
  { section: "sensors", key: "mqtt", label: "MQTT", kind: "switch" },
  { section: "sensors", key: "mqtt_port", label: "MQTT port", kind: "int", min: 1, max: 65535 },
  {
    section: "sensors",
    key: "mqtt_anonymous",
    label: "Let devices in without a login",
    kind: "switch",
    hint: "Off: devices sign in with a hub login (Sensors → Hub logins)",
  },
  { section: "sensors", key: "syslog", label: "Syslog", kind: "switch" },
  {
    section: "sensors",
    key: "syslog_port",
    label: "Syslog port (UDP and TCP)",
    kind: "int",
    min: 1,
    max: 65535,
    hint: "Ports below 1024 need extra rights; 5514 avoids that",
  },
  {
    section: "sensors",
    key: "syslog_networks",
    label: "Networks syslog is taken from",
    kind: "lines",
    mono: true,
    hint: "One per line, like 192.168.1.0/24. Syslog has no login, so anything else is ignored",
  },
  {
    section: "sensors",
    key: "max_payload_kb",
    label: "Largest message (KB)",
    kind: "int",
    min: 1,
    max: 16384,
  },
  {
    section: "sensors",
    key: "store",
    label: "Keep",
    kind: "select",
    options: [
      { value: "all", label: "Every reading" },
      { value: "changes", label: "Only changes (and one an hour)" },
      { value: "summary", label: "Hourly summaries only" },
      { value: "none", label: "Nothing: count and drop" },
    ],
    hint: "For sensors that don’t choose for themselves",
  },
  {
    section: "sensors",
    key: "raw_days",
    label: "Keep readings for (days)",
    kind: "int",
    min: 1,
    max: 36500,
    nullable: true,
    hint: "Empty keeps them for good",
  },
  {
    section: "sensors",
    key: "important_days",
    label: "Keep warnings and errors for (days)",
    kind: "int",
    min: 1,
    max: 36500,
    nullable: true,
    hint: "Log lines of warning or worse, when longer than readings",
  },
  {
    section: "sensors",
    key: "rollup_days",
    label: "Keep hourly summaries for (days)",
    kind: "int",
    min: 1,
    max: 36500,
    nullable: true,
    hint: "Charts use these",
  },
  {
    section: "sensors",
    key: "max_per_minute",
    label: "Most readings a minute, per stream",
    kind: "int",
    min: 1,
    max: 100000,
    hint: "More are counted and dropped",
  },
  {
    section: "sensors",
    key: "triage",
    label: "Sort new kinds of log line with the decision model",
    kind: "switch",
    hint: "One question per kind of line, never per line: routine, notable or alert",
  },
  // Fedora (opt-in)
  {
    section: "fedora",
    key: "url",
    label: "Fedora REST API",
    kind: "text",
    nullable: true,
    mono: true,
    placeholder: "http://fedora:8080/fcrepo/rest",
    hint: "Empty keeps Fedora off. With the compose profile `fedora`, it is http://fedora:8080/fcrepo/rest",
  },
  {
    section: "fedora",
    key: "enabled",
    label: "Keep the copy in step",
    kind: "switch",
    hint: "Off pauses sending; nothing is deleted",
  },
  { section: "fedora", key: "user", label: "User", kind: "text", nullable: true, mono: true },
  { section: "fedora", key: "password", label: "Password", kind: "secret" },
  {
    section: "fedora",
    key: "root",
    label: "Folder in Fedora",
    kind: "text",
    mono: true,
    hint: "The container everything goes under, so Fedora can hold other things too",
  },
  {
    section: "fedora",
    key: "files",
    label: "Send recordings’ files",
    kind: "switch",
    hint: "Off sends descriptions only",
  },
  {
    section: "fedora",
    key: "max_file_mb",
    label: "Largest file to send (MB)",
    kind: "int",
    min: 0,
    max: 1000000,
    hint: "0 sends files of any size",
  },
  { section: "fedora", key: "sync_seconds", label: "Send changes every (seconds)", kind: "int", min: 10, max: 86400 },
  {
    section: "fedora",
    key: "full_hours",
    label: "Compare everything every (hours)",
    kind: "int",
    min: 1,
    max: 720,
    hint: "Catches what analysis changed, new recordings and deletions",
  },
  {
    section: "tokens",
    key: "default_days",
    label: "A new key lasts (days)",
    kind: "int",
    min: 1,
    max: 3650,
  },
  {
    section: "tokens",
    key: "max_days",
    label: "At most (days)",
    kind: "int",
    min: 1,
    max: 3650,
  },
  {
    section: "tokens",
    key: "never_expire",
    label: "Allow keys that never expire",
    kind: "switch",
    hint: "Off: every key expires. Keys made before a change keep their expiry; revoke them below.",
  },
  // Notifications
  {
    section: "notifications",
    key: "enabled",
    label: "Send notifications",
    kind: "switch",
    hint: "Off: nothing is sent, and what happens meanwhile isn’t sent later",
  },
  {
    section: "notifications",
    key: "networks",
    label: "Private networks targets may be in",
    kind: "lines",
    mono: true,
    hint: "One per line, like 192.168.1.0/24 or 172.16.0.0/12 for Docker. Without one, targets must be public addresses; a Matterbridge on your network needs its network here",
  },
  {
    section: "notifications",
    key: "app_url",
    label: "Web app address for links",
    kind: "text",
    nullable: true,
    mono: true,
    placeholder: "https://lens.example.org",
    hint: "Messages link to runs and recordings here. Empty: the server’s FRONTEND_URL",
  },
  {
    section: "notifications",
    key: "poll_seconds",
    label: "Look for news every (seconds)",
    kind: "int",
    min: 1,
    max: 3600,
  },
  {
    section: "notifications",
    key: "max_attempts",
    label: "Tries per message",
    kind: "int",
    min: 1,
    max: 20,
    hint: "A failed send waits 30 seconds, then four times longer each time, up to 6 hours",
  },
  // Telemetry (opt-in)
  {
    section: "telemetry",
    key: "enabled",
    label: "Send telemetry",
    kind: "switch",
    hint: "Off by default; while off, nothing is collected or sent",
  },
  {
    section: "telemetry",
    key: "endpoint",
    label: "OTLP endpoint",
    kind: "text",
    nullable: true,
    mono: true,
    placeholder: "http://localhost:4318",
    hint: "An OpenTelemetry collector’s OTLP/HTTP address; traces go to /v1/traces and metrics to /v1/metrics under it",
  },
  { section: "telemetry", key: "headers", label: "Headers", kind: "secret" },
  {
    section: "telemetry",
    key: "traces",
    label: "Traces",
    kind: "switch",
    hint: "API requests, jobs and their steps, routines, workflows and model calls",
  },
  {
    section: "telemetry",
    key: "metrics",
    label: "Metrics",
    kind: "switch",
    hint: "Durations, job outcomes, model tokens and estimated cost",
  },
  {
    section: "telemetry",
    key: "sample_ratio",
    label: "Share of traces kept",
    kind: "number",
    min: 0,
    max: 1,
    hint: "1 keeps every trace, 0.1 one in ten",
  },
  {
    section: "telemetry",
    key: "export_seconds",
    label: "Send metrics every (seconds)",
    kind: "int",
    min: 5,
    max: 3600,
  },
  {
    section: "telemetry",
    key: "service_name",
    label: "Service name",
    kind: "text",
    mono: true,
    hint: "How this server shows in your tracing tool",
  },
  {
    section: "telemetry",
    key: "prices",
    label: "Model prices for cost estimates",
    kind: "lines",
    mono: true,
    placeholder: "gpt-4o-mini 0.15 0.60",
    hint: "One model per line: its name, then the input and output price in dollars per million tokens. The AI assistant’s prices count for the configured model",
  },
  {
    section: "tokens",
    key: "oauth_enabled",
    label: "Let apps and AI assistants sign in",
    kind: "switch",
    hint: "Claude, ChatGPT, Cursor and other apps sign in with a person’s Lens account (OAuth) and act with their roles. Off: apps need an API key, and the ones people allowed stop working until it’s back on",
  },
  {
    section: "tokens",
    key: "oauth_access_minutes",
    label: "An app’s access token lasts (minutes)",
    kind: "int",
    min: 5,
    max: 1440,
    hint: "Apps people sign in to with their Lens account renew it by themselves",
  },
  {
    section: "tokens",
    key: "oauth_refresh_days",
    label: "An app stays signed in for (days)",
    kind: "int",
    min: 1,
    max: 3650,
    hint: "Counted from when the app last renewed its access; at most as long as a key may last",
  },
  {
    section: "uploads",
    key: "max_mb",
    label: "Largest audio or video file (MB)",
    kind: "int",
    min: 1,
    max: 1_000_000,
  },
  {
    section: "uploads",
    key: "chunk_mb",
    label: "Piece size (MB)",
    kind: "int",
    min: 1,
    max: 64,
    hint: "How much the web app sends per request; keep it under the body limit of any proxy in front of the server",
  },
  {
    section: "uploads",
    key: "expire_hours",
    label: "Keep unfinished uploads for (hours)",
    kind: "int",
    min: 1,
    max: 720,
    hint: "After the last piece arrived; then what arrived is deleted",
  },
  {
    section: "uploads",
    key: "extensions",
    label: "Types people can upload",
    kind: "checks",
    options: UPLOAD_TYPES.map((e) => ({ value: e, label: e.slice(1) })),
  },
  // Documents
  {
    section: "documents",
    key: "page_pixels",
    label: "Page size (pixels, longest side)",
    kind: "int",
    min: 800,
    max: 6000,
    hint: "How large each page is drawn to look at",
  },
  {
    section: "documents",
    key: "thumb_pixels",
    label: "Thumbnail size (pixels)",
    kind: "int",
    min: 120,
    max: 800,
  },
  {
    section: "documents",
    key: "ocr_below_chars",
    label: "Read a page by OCR below (characters)",
    kind: "int",
    min: 0,
    max: 5000,
    hint: "Pages with less text than this are scans: their text is read from the picture",
  },
  {
    section: "documents",
    key: "max_pages",
    label: "Pages read, at most",
    kind: "int",
    min: 1,
    max: 50000,
  },
  {
    section: "documents",
    key: "convert_seconds",
    label: "Time to make a PDF (seconds)",
    kind: "int",
    min: 10,
    max: 3600,
    hint: "For a Word or other Office file, text, a web page or an email; longer and its job fails",
  },
  {
    section: "documents",
    key: "attachment_resources",
    label: "Make an email’s attachments resources of their own",
    kind: "switch",
    hint: "Documents, images, audio, video and emails attached to an email; they’re kept as its files either way",
  },
  // IIIF & metadata
  {
    section: "iiif",
    key: "base_url",
    label: "Public base URL",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "https://iiif.example.org",
    hint: "Every Manifest and Collection id starts with this. Must be HTTPS and stable.",
  },
  {
    section: "iiif",
    key: "rights",
    label: "Default rights",
    kind: "select",
    nullable: true,
    options: [],
  },
  {
    section: "iiif",
    key: "attribution",
    label: "Default attribution",
    kind: "text",
    nullable: true,
  },
  {
    section: "iiif",
    key: "provider.name",
    label: "Provider",
    kind: "text",
    nullable: true,
    placeholder: "Organisation name",
  },
  {
    section: "iiif",
    key: "provider.homepage",
    label: "Provider homepage",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "https://…",
  },
  {
    section: "iiif",
    key: "provider.logo",
    label: "Provider logo",
    kind: "text",
    mono: true,
    nullable: true,
    placeholder: "https://…/logo.png",
  },
  {
    section: "iiif",
    key: "default_language",
    label: "Default metadata language",
    kind: "text",
    mono: true,
    hint: "A code such as en, or none when titles have no language",
  },
  {
    section: "iiif",
    key: "layers",
    label: "Published annotation layers",
    kind: "checks",
    options: LAYERS,
  },
  {
    section: "iiif",
    key: "allowed_origins",
    label: "Viewers allowed to request access",
    kind: "lines",
    mono: true,
    hint: "Origins of viewer sites that may get IIIF access tokens, one per line; * allows any",
  },
  {
    section: "iiif",
    key: "token_minutes",
    label: "IIIF access tokens last (minutes)",
    kind: "int",
    min: 1,
    max: 1440,
  },
];

export function fieldsOf(section: SectionId): FieldSpec[] {
  const s = SECTIONS.find((x) => x.id === section);
  return s ? FIELDS.filter((f) => s.backend.includes(f.section)) : [];
}

export const fieldId = (f: Pick<FieldSpec, "section" | "key">) => `${f.section}.${f.key}`;

export function getPath(values: SectionValues | undefined, key: string): unknown {
  return key
    .split(".")
    .reduce<unknown>((v, k) => (v && typeof v === "object" ? (v as Record<string, unknown>)[k] : undefined), values);
}

/** The value as a form control holds it: text for inputs, booleans for switches, arrays for checks. */
export function toUi(f: FieldSpec, v: unknown): unknown {
  switch (f.kind) {
    case "switch":
      return f.section === "speakers" && f.key === "cross_namespace" ? v !== "off" : Boolean(v);
    case "checks":
      return Array.isArray(v) ? v.map(String) : [];
    case "lines":
      if (f.section === "telemetry" && f.key === "prices") return pricesToLines(v);
      return Array.isArray(v) ? v.join("\n") : "";
    case "days":
      return typeof v === "number" ? String(Math.round((v / 24) * 100) / 100) : "";
    case "secret":
      return undefined;
    default:
      return v == null ? "" : String(v);
  }
}

export type Parsed = { value: unknown } | { error: string };

export type Prices = Record<string, { input: number; output: number }>;

/** telemetry.prices as lines: "model input output". */
export function pricesToLines(v: unknown): string {
  if (!v || typeof v !== "object") return "";
  return Object.entries(v as Prices)
    .map(([model, p]) => `${model} ${p?.input ?? 0} ${p?.output ?? 0}`)
    .join("\n");
}

/** Lines of "model input output" (dollars per million tokens) back to telemetry.prices. */
export function linesToPrices(text: string): Parsed {
  const out: Prices = {};
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  for (let i = 0; i < lines.length; i++) {
    const parts = lines[i].split(/\s+/);
    const nums = parts.slice(-2).map(Number);
    if (parts.length < 3 || nums.some((n) => !Number.isFinite(n) || n < 0))
      return { error: `Line ${i + 1}: write the model, then its input and output price, like gpt-4o-mini 0.15 0.60` };
    out[parts.slice(0, -2).join(" ")] = { input: nums[0], output: nums[1] };
  }
  return { value: out };
}

/** Parses a control's value back to what the backend stores, checking it like the backend does. */
export function parse(f: FieldSpec, ui: unknown): Parsed {
  switch (f.kind) {
    case "switch":
      return {
        value: f.section === "speakers" && f.key === "cross_namespace" ? (ui ? "suggest" : "off") : Boolean(ui),
      };
    case "checks":
      return { value: ui as string[] };
    case "lines":
      if (f.section === "telemetry" && f.key === "prices") return linesToPrices(String(ui ?? ""));
      return {
        value: String(ui ?? "")
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean),
      };
    case "secret":
      return { value: ui };
    case "number":
    case "int":
    case "days": {
      const s = String(ui ?? "").trim();
      if (!s) return f.nullable ? { value: null } : { error: "Enter a number" };
      const n = Number(s);
      if (!Number.isFinite(n)) return { error: "Enter a number" };
      if (f.kind === "int" && !Number.isInteger(n)) return { error: "Use a whole number" };
      if (f.min != null && n < f.min)
        return {
          error: f.kind === "days" ? "Use at least 1 hour (0.05 days)" : `Use ${f.min} or more`,
        };
      if (f.max != null && n > f.max) return { error: `Use ${f.max} or less` };
      if (f.kind === "days") return { value: Math.max(1, Math.round(n * 24)) };
      return { value: n };
    }
    case "select":
      if (f.key === "frame_width") return { value: Number(ui) };
      return { value: ui === "" && f.nullable ? null : ui };
    default: {
      const s = String(ui ?? "").trim();
      return { value: s === "" && f.nullable ? null : s };
    }
  }
}

const HOST_RX = /^(\*|\[[0-9a-f:]+\]|[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*)$/i;
const ENTITY_TYPES = ["PERSON", "ORG", "PRODUCT", "PLACE", "EVENT", "WORK", "TERM"];
const URL_RX = /^https?:\/\/[^\s]+$/;
const TUNNEL_HOST_RX = /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$/i;

/** "Line 4: missing “|” between name and type" for the custom vocabulary. */
export function gazetteerErrors(lines: string[]): string | null {
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.includes("|")) {
      const [name, type] = line.split("|").map((x) => x.trim());
      if (!name) return `Line ${i + 1}: the name is empty`;
      if (type && !ENTITY_TYPES.includes(type.toUpperCase()))
        return `Line ${i + 1}: “${type}” isn’t a type — use ${ENTITY_TYPES.join(", ")}`;
    } else {
      const last = line.split(/\s+/).pop() ?? "";
      if (line.includes(" ") && ENTITY_TYPES.includes(last) && last === last.toUpperCase())
        return `Line ${i + 1}: missing “|” between name and type`;
    }
  }
  return null;
}

/** Checks across fields and the backend's own rules. Values are the parsed values of the whole section group. */
export function crossErrors(values: Record<string, unknown>): Record<string, string> {
  const e: Record<string, string> = {};
  const n = (k: string) => values[k] as number | null | undefined;
  const match = n("speakers.match_threshold");
  const review = n("speakers.review_threshold");
  if (typeof match === "number" && typeof review === "number" && !(0 <= review && review <= match && match <= 1))
    e["speakers.review_threshold"] = "Thresholds must satisfy 0 ≤ review ≤ match ≤ 1";
  const fm = n("video.face_match_threshold");
  const fr = n("video.face_review_threshold");
  if (typeof fm === "number" && typeof fr === "number" && fr > fm)
    e["video.face_review_threshold"] = "The review threshold can’t be above auto-match";
  const dd = n("tokens.default_days");
  const md = n("tokens.max_days");
  if (typeof dd === "number" && typeof md === "number" && dd > md)
    e["tokens.default_days"] = "A new key can’t last longer than the most a key may last";
  const od = n("tokens.oauth_refresh_days");
  if (typeof od === "number" && typeof md === "number" && od > md)
    e["tokens.oauth_refresh_days"] = "An app can’t stay signed in longer than the most a key may last";
  const mn = n("diarize.min_speakers");
  const mx = n("diarize.max_speakers");
  if (typeof mn === "number" && typeof mx === "number" && mn > mx)
    e["diarize.max_speakers"] = "Max speakers can’t be below min speakers";
  const hosts = values["server.allowed_hosts"] as string[] | undefined;
  if (hosts) {
    if (!hosts.length) e["server.allowed_hosts"] = "List at least one host name (or * for any)";
    else {
      const bad = hosts.find((h) => !HOST_RX.test(h));
      if (bad) e["server.allowed_hosts"] = `“${bad}” isn’t a host name (no scheme, path or port)`;
    }
  }
  const origins = values["server.embed_frame_ancestors"] as string[] | undefined;
  const badOrigin = origins?.find(
    (o) => o !== "'self'" && o !== "'none'" && o !== "*" && !/^https?:\/\/[^/\s]+$/.test(o),
  );
  if (badOrigin)
    e["server.embed_frame_ancestors"] = `“${badOrigin}” isn’t an origin like https://blog.example.org or 'self'`;
  const types = values["uploads.extensions"] as string[] | undefined;
  if (types && !types.length) e["uploads.extensions"] = "Pick at least one type";
  const gaz = values["analysis.gazetteer"] as string[] | undefined;
  const g = gaz ? gazetteerErrors(gaz) : null;
  if (g) e["analysis.gazetteer"] = g;
  const llm = values["llm.base_url"] as string | null | undefined;
  if (llm && !URL_RX.test(llm)) e["llm.base_url"] = "Use an http(s) address, such as https://api.example.org/v1";
  const embed = values["embeddings.base_url"] as string | null | undefined;
  if (embed && !URL_RX.test(embed))
    e["embeddings.base_url"] = "Use an http(s) address, such as http://localhost:11434/v1";
  if (values["embeddings.model"] === "" || values["embeddings.model"] === null)
    e["embeddings.model"] = "Name the embedding model, such as nomic-embed-text";
  const base = values["iiif.base_url"] as string | null | undefined;
  if (base && !URL_RX.test(base)) e["iiif.base_url"] = "Use an http(s) address";
  const appUrl = values["notifications.app_url"] as string | null | undefined;
  if (appUrl && !URL_RX.test(appUrl)) e["notifications.app_url"] = "Use an http(s) address";
  const nets = values["notifications.networks"] as string[] | undefined;
  const badNet = nets?.find((x) => !/^[0-9a-f.:]+(\/\d{1,3})?$/i.test(x));
  if (badNet) e["notifications.networks"] = `“${badNet}” isn’t a network like 192.168.1.0/24`;
  const sNets = values["sensors.syslog_networks"] as string[] | undefined;
  const badSNet = sNets?.find((x) => !/^[0-9a-f.:]+(\/\d{1,3})?$/i.test(x));
  if (badSNet) e["sensors.syslog_networks"] = `“${badSNet}” isn’t a network like 192.168.1.0/24`;
  const mode = values["tunnel.mode"] as string | undefined;
  const tHost = values["tunnel.hostname"] as string | undefined;
  if ((mode === "token" || mode === "managed") && "tunnel.hostname" in values && !TUNNEL_HOST_RX.test(tHost ?? ""))
    e["tunnel.hostname"] = "Enter the public hostname, like lens.example.com";
  const tOrigin = values["tunnel.origin"] as string | undefined;
  if (tOrigin && !URL_RX.test(tOrigin)) e["tunnel.origin"] = "Use an http(s) address, such as http://frontend:3000";
  const otlp = values["telemetry.endpoint"] as string | null | undefined;
  if (otlp && !URL_RX.test(otlp)) e["telemetry.endpoint"] = "Use an http(s) address, such as http://localhost:4318";
  else if (otlp && /\/v1\/(traces|metrics)\/?$/.test(otlp))
    e["telemetry.endpoint"] = "Use the collector’s base address, without /v1/traces or /v1/metrics";
  else if (values["telemetry.enabled"] === true && "telemetry.endpoint" in values && !otlp)
    e["telemetry.endpoint"] = "Set the collector’s address to send telemetry to";
  const rights = values["iiif.rights"] as string | null | undefined;
  if (rights && !RIGHTS_RX.test(rights))
    e["iiif.rights"] = "Pick a Creative Commons licence or a RightsStatements.org statement";
  for (const k of ["iiif.provider.homepage", "iiif.provider.logo"]) {
    const v = values[k] as string | null | undefined;
    if (v && !URL_RX.test(v)) e[k] = "Use an http(s) address";
  }
  if ((values["iiif.provider.homepage"] || values["iiif.provider.logo"]) && !values["iiif.provider.name"])
    e["iiif.provider.name"] = "The provider needs a name";
  const lang = values["iiif.default_language"] as string | undefined;
  if (lang != null && !/^(none|[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,8})*)$/.test(lang))
    e["iiif.default_language"] = "Use a code such as en or pt-BR, or none";
  const origins2 = values["iiif.allowed_origins"] as string[] | undefined;
  const badViewer = origins2?.find((o) => o !== "*" && !/^https?:\/\/[^/\s]+$/.test(o));
  if (badViewer) e["iiif.allowed_origins"] = `“${badViewer}” isn’t an origin like https://viewer.example.org`;
  return e;
}

export function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

export type Change = {
  id: string;
  field: FieldSpec;
  before: unknown;
  after: unknown;
};

/** Human text for a value in the review dialog. */
export function show(f: FieldSpec, v: unknown): string {
  if (f.kind === "secret") return v === "" ? "cleared" : v ? "(changed)" : "unchanged";
  if (v == null || v === "") return f.nullable ? "not set" : "—";
  if (f.kind === "switch") return f.key === "cross_namespace" ? (v === "off" ? "off" : "on") : v ? "on" : "off";
  if (f.kind === "days") return `${Math.round(((v as number) / 24) * 100) / 100} days`;
  if (f.key === "rights") return rightsShort(v as string);
  if (f.section === "telemetry" && f.key === "prices") {
    const n = Object.keys((v as Prices) ?? {}).length;
    return n ? `${n} model${n === 1 ? "" : "s"}` : "none";
  }
  if (Array.isArray(v)) {
    if (f.kind === "checks" && f.section === "ai") return `${v.length} off`;
    return v.length > 3 ? `${v.length} ${f.kind === "lines" ? "lines" : "items"}` : v.join(", ") || "none";
  }
  const opt = f.options?.find((o) => o.value === String(v));
  return opt ? opt.label : `${v}${f.unit ? ` ${f.unit}` : ""}`;
}

/** Why a change matters, when it isn't obvious. */
export function why(c: Change): string | null {
  const id = fieldId(c.field);
  if (id === "server.session_hours") return "Applies from each person’s next sign-in or session refresh.";
  if (id === "server.allowed_hosts") return "Requests to any other host name are refused.";
  if (id === "server.trusted_proxies") return "IP groups match the address these proxies report for each visitor.";
  if (id === "server.embed_frame_ancestors") {
    const before = (c.before as string[]) ?? [];
    const after = (c.after as string[]) ?? [];
    const added = after.filter((x) => !before.includes(x));
    const removed = before.filter((x) => !after.includes(x));
    return (
      [
        added.length ? `Adds ${added.join(", ")}.` : "",
        removed.length ? `Removes ${removed.join(", ")}; players embedded there stop loading.` : "",
      ]
        .filter(Boolean)
        .join(" ") || null
    );
  }
  if (id === "search.stemming") return "Search keeps the old index until you rebuild it (Reindex).";
  if (id === "embeddings.model" || id === "embeddings.document_prefix")
    return "Vectors from another model can’t be compared: search by meaning stops until recordings are indexed again (the hourly routine, or Index now).";
  if (id === "iiif.base_url") return "Every IIIF identifier changes.";
  if (id === "transcribe.engine" || id === "transcribe.language")
    return "Applies to new transcriptions; existing transcripts stay until reprocessed.";
  if (id === "workers.inline") return "Takes effect when the server restarts.";
  if (id === "telemetry.enabled")
    return c.after
      ? "The server and its workers start sending traces and metrics to the endpoint within a few seconds."
      : "Nothing more is sent; what was sent stays with your collector.";
  if (id === "telemetry.endpoint" && c.before && c.after !== c.before) {
    const origin = (u: unknown) => {
      try {
        return new URL(String(u)).origin;
      } catch {
        return String(u);
      }
    };
    if (origin(c.before) !== origin(c.after))
      return "Saved headers are removed, since they were for the old collector: enter them again if the new one needs them.";
  }
  if (id === "telemetry.headers")
    return c.after === "" ? "The headers are removed." : "Sent with every export; stored encrypted.";
  if (id === "llm.api_key")
    return c.after === ""
      ? "The key is removed; LLM steps and chat stop until a new one is set."
      : "The old key keeps working until you save.";
  return null;
}

function isBlank(v: unknown): boolean {
  return v == null || v === "" || (Array.isArray(v) && !v.length);
}

/**
 * The PUT body per backend section for a set of changes. Nested keys (transcribe.whisper.model) send the whole dict
 * with the change applied, so its other keys keep their current values; a provider left with nothing becomes null.
 */
export function buildPatches(changes: Change[], view: SettingsView): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const c of changes) {
    const section = c.field.section;
    const [top, ...rest] = c.field.key.split(".");
    const patch = (out[section] ??= {});
    if (!rest.length) {
      patch[top] = c.after;
      continue;
    }
    const current = (patch[top] as Record<string, unknown> | undefined) ?? {
      ...((view[section]?.values?.[top] as Record<string, unknown> | null | undefined) ?? {}),
    };
    let node = current;
    for (const k of rest.slice(0, -1))
      node = (node[k] = {
        ...((node[k] as Record<string, unknown>) ?? {}),
      }) as Record<string, unknown>;
    node[rest[rest.length - 1]] = c.after;
    patch[top] = current;
  }
  const provider = out.iiif?.provider as Record<string, unknown> | undefined;
  if (provider && Object.values(provider).every(isBlank)) out.iiif.provider = null;
  return out;
}

/** Which field a refusal from PUT /settings/{section} is about, and the message to show under it. */
export function serverError(section: string, message: string): { field: string | null; message: string } {
  if (/review.*match|thresholds must/i.test(message))
    return {
      field: "speakers.review_threshold",
      message: "Thresholds must satisfy 0 ≤ review ≤ match ≤ 1",
    };
  const lock = message.match(/leaves out ([^\s,]+)/i);
  if (lock)
    return {
      field: "server.allowed_hosts",
      message: `This list leaves out ${lock[1]}, the address this app reaches the server at. Saving it would lock everyone out, so it’s refused. Add it back first.`,
    };
  const key = message.match(/\b([a-z_]+)\.([a-z_]+)\b/);
  if (key && FIELDS.some((f) => f.section === key[1] && (f.key === key[2] || f.key.startsWith(`${key[2]}.`)))) {
    const f = FIELDS.find((x) => x.section === key[1] && (x.key === key[2] || x.key.startsWith(`${key[2]}.`)))!;
    return {
      field: fieldId(f),
      message: message[0].toUpperCase() + message.slice(1),
    };
  }
  return {
    field: null,
    message: message ? message[0].toUpperCase() + message.slice(1) : `Couldn’t save ${section}`,
  };
}
