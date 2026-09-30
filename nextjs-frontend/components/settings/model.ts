/**
 * Settings saved in the app (Settings ST1–ST3, IIIF & metadata MD5): the backend's sections and fields, how each field
 * is shown and parsed, validation that mirrors the backend (app/domain/settings.py), and the list of changes shown in
 * "Review & save".
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
  } & Record<string, unknown>;
};

export type Kind =
  | "text"
  | "number"
  | "int"
  | "select"
  | "switch"
  | "lines"
  | "checks"
  | "secret"
  | "pills"
  | "cards"
  | "days";
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
  | "voice-ids"
  | "analysis"
  | "llm"
  | "ai"
  | "search"
  | "reports"
  | "video"
  | "workers"
  | "access"
  | "iiif"
  | "startup";

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
    id: "ai",
    label: "AI assistant",
    backend: ["ai"],
    description: "What the chat assistant may do with tools, and when a batch run needs a typed confirmation.",
  },
  {
    id: "search",
    label: "Search",
    backend: ["search"],
    description: "How transcripts are indexed for search.",
  },
  {
    id: "reports",
    label: "Reports and graph",
    backend: ["reports", "graph"],
    description: "What reports carry, and how big the knowledge graph gets.",
  },
  {
    id: "video",
    label: "Video, OCR and faces",
    backend: ["video"],
    description: "Shots and keyframes, text on screen, and faces in video recordings.",
  },
  {
    id: "workers",
    label: "Workers",
    backend: ["workers"],
    description: "The workers inside the server, and how the job queue retries.",
  },
  {
    id: "access",
    label: "Access & embedding",
    backend: ["server"],
    description:
      "Who can reach the server, how it tells visitors’ addresses, which sites may embed the player, and how long sessions last.",
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
  "analyze",
  "summarize",
  "llm",
  "report",
  "export",
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
  {
    name: "graph_neighbours",
    label: "Explore the graph (neighbours)",
    acts: false,
  },
  { name: "speaker_stats", label: "Speakers’ talk time", acts: false },
  { name: "run_template", label: "Run a template on recordings", acts: true },
  {
    name: "propose_entity_change",
    label: "Propose entity merges, renames, type changes",
    acts: true,
  },
];

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
    hint: "Lines below are kept but flagged",
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
    label: "Largest upload (MB)",
    kind: "int",
    min: 1,
    max: 1_000_000,
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
  const gaz = values["analysis.gazetteer"] as string[] | undefined;
  const g = gaz ? gazetteerErrors(gaz) : null;
  if (g) e["analysis.gazetteer"] = g;
  const llm = values["llm.base_url"] as string | null | undefined;
  if (llm && !URL_RX.test(llm)) e["llm.base_url"] = "Use an http(s) address, such as https://api.example.org/v1";
  const base = values["iiif.base_url"] as string | null | undefined;
  if (base && !URL_RX.test(base)) e["iiif.base_url"] = "Use an http(s) address";
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
  if (id === "iiif.base_url") return "Every IIIF identifier changes.";
  if (id === "transcribe.engine" || id === "transcribe.language")
    return "Applies to new transcriptions; existing transcripts stay until reprocessed.";
  if (id === "workers.inline") return "Takes effect when the server restarts.";
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
