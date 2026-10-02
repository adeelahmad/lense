/**
 * Storage connections and watched folders as the Sources screens show them: labels, one-line summaries, health in
 * the backend's own words, the rclone token paste check, and what a watched folder's settings mean in words.
 */
import type { Source, Watch, WatchPreview } from "@/app/openapi-client/types.gen";
import { plural } from "@/lib/format";

export type SourceType = Source["type"];

export const TYPE_ORDER: SourceType[] = [
  "s3",
  "dropbox",
  "drive",
  "onedrive",
  "sftp",
  "smb",
  "webdav",
  "local",
  "imap",
  "ical",
];

/** Short names for the type picker and rows (the backend's labels are longer). */
export const TYPE_NAME: Record<SourceType, string> = {
  s3: "S3 / compatible",
  dropbox: "Dropbox",
  drive: "Google Drive",
  onedrive: "OneDrive",
  sftp: "SFTP",
  smb: "SMB",
  webdav: "WebDAV",
  local: "This machine",
  imap: "Email (IMAP)",
  ical: "Calendar feed",
};

/** How to name the storage itself in sentences ("Files in the bucket aren’t touched"). */
export const STORAGE_NOUN: Record<SourceType, string> = {
  s3: "the bucket",
  dropbox: "Dropbox",
  drive: "Google Drive",
  onedrive: "OneDrive",
  sftp: "the SFTP server",
  smb: "the share",
  webdav: "the WebDAV server",
  local: "the folder",
  imap: "the mailbox",
  ical: "the calendar",
};

/** Field labels for the backend's parameter and secret names. */
export const FIELD_LABEL: Record<string, string> = {
  provider: "Provider",
  region: "Region",
  endpoint: "Endpoint",
  access_key_id: "Access key ID",
  secret_access_key: "Secret access key",
  token: "Token",
  scope: "Scope",
  root_folder_id: "Root folder ID",
  drive_id: "Drive ID",
  drive_type: "Drive type",
  host: "Host",
  user: "User",
  port: "Port",
  pass: "Password",
  key_pem: "Private key",
  domain: "Domain",
  url: "URL",
  vendor: "Vendor",
  security: "Security",
};

export const S3_PROVIDERS = [
  { value: "AWS", label: "AWS" },
  { value: "Wasabi", label: "Wasabi" },
  { value: "Minio", label: "MinIO" },
  { value: "Cloudflare", label: "Cloudflare R2" },
  { value: "Other", label: "Other" },
];
export const DRIVE_SCOPES = [
  { value: "drive.readonly", label: "Read only (drive.readonly)" },
  { value: "drive", label: "Full (drive)" },
];
export const WEBDAV_VENDORS = [
  { value: "nextcloud", label: "Nextcloud" },
  { value: "owncloud", label: "ownCloud" },
  { value: "sharepoint", label: "SharePoint" },
  { value: "other", label: "Other" },
];

export const IMAP_SECURITY = [
  { value: "ssl", label: "SSL/TLS (port 993)" },
  { value: "starttls", label: "STARTTLS (port 143)" },
  { value: "none", label: "None (not encrypted)" },
];

const str = (v: unknown) => (v == null ? "" : String(v)).trim();

/** "S3 · AWS eu-central-1", "SFTP · recorder@calls-gw:22", "Drive · read only"... */
export function sourceSubtitle(s: Pick<Source, "type" | "params">): string {
  const p = (s.params ?? {}) as Record<string, unknown>;
  switch (s.type) {
    case "s3": {
      const prov = S3_PROVIDERS.find((x) => x.value === str(p.provider))?.label ?? (str(p.provider) || "S3");
      return ["S3", [prov, str(p.region)].filter(Boolean).join(" "), str(p.endpoint)].filter(Boolean).join(" · ");
    }
    case "dropbox":
      return "Dropbox · token";
    case "drive":
      return `Drive · ${str(p.scope) === "drive" ? "full access" : "read only"}${str(p.root_folder_id) ? " · one folder" : ""}`;
    case "onedrive":
      return `OneDrive · token${str(p.drive_type) ? ` · ${str(p.drive_type)}` : ""}`;
    case "sftp":
      return `SFTP · ${str(p.user) ? `${str(p.user)}@` : ""}${str(p.host) || "no host"}${str(p.port) && str(p.port) !== "22" ? `:${str(p.port)}` : ":22"}`;
    case "smb":
      return `SMB · ${str(p.domain) ? `${str(p.domain)}\\` : ""}${str(p.user) ? `${str(p.user)}@` : ""}${str(p.host) || "no host"}`;
    case "webdav":
      return `WebDAV · ${str(p.url) || "no URL"}`;
    case "imap":
      return `IMAP · ${str(p.user) ? `${str(p.user)}@` : ""}${str(p.host) || "no host"}${str(p.port) ? `:${str(p.port)}` : ""}`;
    case "ical":
      return "Calendar feed · address kept secret";
    default:
      return "Folder on this machine";
  }
}

export type Health = { tone: "ok" | "bad" | "unknown"; text: string };

/** "OK · checked 14:02" or the backend's own error text, word for word. */
export function healthOf(h: Source["health"], now = new Date()): Health {
  if (!h) return { tone: "unknown", text: "Not tested yet" };
  if (h.ok) return { tone: "ok", text: `OK · checked ${checkedAt(h.checked_at, now)}` };
  return {
    tone: "bad",
    text: h.error?.trim() || "The test failed without a message",
  };
}

function checkedAt(iso: string | null | undefined, now: Date): string {
  if (!iso) return "just now";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (d.toDateString() === now.toDateString()) return hm;
  return `${d.getDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()]} ${hm}`;
}

// ---------- rclone authorize ----------

export type TokenCheck =
  | { ok: true; token: string; expiry?: Date; refreshes: boolean }
  | { ok: false; error: string }
  | { ok: null };

/**
 * `rclone authorize dropbox` prints a JSON token between arrows ("Paste the following into your remote machine --->"
 * … "<---End paste"). Accept the whole output or just the JSON; hand the backend the compact JSON.
 */
export function parseRcloneToken(text: string): TokenCheck {
  const t = text.trim();
  if (!t) return { ok: null };
  const start = t.indexOf("{");
  const end = t.lastIndexOf("}");
  if (start < 0 || end <= start)
    return {
      ok: false,
      error: "That isn’t a token — paste the JSON that rclone printed, including the braces.",
    };
  let v: unknown;
  try {
    v = JSON.parse(t.slice(start, end + 1));
  } catch {
    return {
      ok: false,
      error: "That isn’t a token — paste the JSON that rclone printed, including the braces.",
    };
  }
  const o = v as {
    access_token?: unknown;
    refresh_token?: unknown;
    expiry?: unknown;
  };
  if (!o || typeof o !== "object" || typeof o.access_token !== "string" || !o.access_token)
    return {
      ok: false,
      error: "This JSON has no access_token. Paste everything rclone printed between the arrows.",
    };
  const expiry = typeof o.expiry === "string" && !Number.isNaN(Date.parse(o.expiry)) ? new Date(o.expiry) : undefined;
  return {
    ok: true,
    token: JSON.stringify(v),
    expiry,
    refreshes: typeof o.refresh_token === "string" && Boolean(o.refresh_token),
  };
}

// ---------- watched folders ----------

const AUDIO = [
  ".m4a",
  ".mp3",
  ".wav",
  ".flac",
  ".ogg",
  ".opus",
  ".aac",
  ".mp4",
  ".webm",
  ".amr",
  ".mov",
  ".mkv",
  ".m4v",
  ".avi",
];
const TRANSCRIPT = [".srt", ".vtt", ".json", ".jsonl", ".ics"];
const DOCUMENT = [
  ".pdf",
  ".doc",
  ".docx",
  ".odt",
  ".rtf",
  ".ppt",
  ".pptx",
  ".odp",
  ".xls",
  ".xlsx",
  ".ods",
  ".txt",
  ".text",
  ".md",
  ".markdown",
  ".mdx",
  ".html",
  ".htm",
  ".eml",
  ".msg",
];
const IMAGE = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp"];

export type SourceFileKind = "audio" | "transcript" | "document" | "image" | "other";

/**
 * What a watched folder would make of a file, by extension (the backend's default lists): PDFs, Office files, text,
 * Markdown, web pages and emails are documents, as they are for new watched folders (those set to audio and
 * transcripts read PDFs, Word and text files as transcripts); subtitles, JSON and calendar events are transcripts.
 */
export function fileKind(name: string): SourceFileKind {
  const n = name.toLowerCase();
  const dot = n.lastIndexOf(".");
  const ext = dot >= 0 ? n.slice(dot) : "";
  if (AUDIO.includes(ext)) return "audio";
  if (DOCUMENT.includes(ext)) return "document";
  if (IMAGE.includes(ext)) return "image";
  if (TRANSCRIPT.includes(ext)) return "transcript";
  return "other";
}

/** "7 files · 2 audio · 3 transcripts · 1 document · 1 image": what there is to pick up (kinds with none left out). */
export function pickupText(p: Pick<WatchPreview, "files" | "audio" | "transcripts" | "documents" | "images">): string {
  const parts = [
    p.audio ? `${p.audio} audio` : null,
    p.transcripts ? plural(p.transcripts, "transcript") : null,
    p.documents ? plural(p.documents, "document") : null,
    p.images ? plural(p.images, "image") : null,
  ].filter(Boolean);
  return [plural(p.files, "file"), ...parts].join(" · ");
}

/** "*.m4a, *.srt" ⇄ ["*.m4a", "*.srt"] */
export function splitPatterns(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export type WatchKinds = NonNullable<Watch["kinds"]>;

/** What a watched folder picks up: the choices, with what each means in a summary. New folders take everything. */
export const WATCH_KINDS: { value: WatchKinds; label: string; summary: string }[] = [
  { value: "all", label: "Everything", summary: "everything" },
  { value: "audio", label: "Audio and video", summary: "audio only" },
  { value: "transcripts", label: "Transcripts (PDFs, Word and text files among them)", summary: "transcripts only" },
  { value: "documents", label: "Documents and images", summary: "documents and images only" },
  {
    value: "both",
    label: "Audio and transcripts (PDFs, Word and text files as transcripts)",
    summary: "audio and transcripts",
  },
];
const PICK: Record<string, string> = Object.fromEntries(WATCH_KINDS.map((k) => [k.value, k.summary]));

/** "→ customer-calls · audio only · every 5 min · wait 60 s · include *.m4a · steps: transcribe, diarize" */
export function watchSummary(
  w: Pick<Watch, "namespace" | "kinds" | "poll_minutes" | "stable_seconds" | "include" | "exclude" | "steps">,
  pipelineName?: string | null,
): string {
  const parts = [
    `→ ${w.namespace ?? "?"}`,
    PICK[w.kinds ?? "all"] ?? "everything",
    `every ${w.poll_minutes ?? 5} min`,
    `wait ${w.stable_seconds ?? 30} s`,
  ];
  if (w.include?.length) parts.push(`include ${w.include.join(", ")}`);
  if (w.exclude?.length) parts.push(`exclude ${w.exclude.join(", ")}`);
  if (w.steps?.length) parts.push(`steps: ${w.steps.join(", ")}`);
  else parts.push(pipelineName ? `pipeline: ${pipelineName}` : "the namespace’s pipeline");
  return parts.join(" · ");
}

export const SCAN_STATS: { key: string; label: string; help: string }[] = [
  { key: "seen", label: "seen", help: "files that matched this scan" },
  { key: "new", label: "new", help: "queued for import" },
  {
    key: "waiting",
    label: "waiting",
    help: "still changing; picked up once they settle",
  },
  {
    key: "skipped",
    label: "skipped",
    help: "already there before watching (no backfill) or excluded",
  },
  { key: "errors", label: "errors", help: "couldn’t be read" },
];

/** "in 4 min", "now", "overdue" for the next scan. */
export function nextScan(
  iso: string | null | undefined,
  enabled: boolean | null | undefined,
  now = Date.now(),
): string {
  if (enabled === false) return "paused";
  if (!iso) return "soon";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const m = Math.round((t - now) / 60_000);
  if (m <= 0) return "due now";
  return m < 60 ? `in ${m} min` : `in ${Math.round(m / 60)} h`;
}

/** The confirmation text for deleting a connection is its name, compared loosely (case, spaces). */
export function confirms(typed: string, name: string): boolean {
  const norm = (s: string) => s.trim().replace(/\s+/g, " ").toLowerCase();
  return norm(typed) !== "" && norm(typed) === norm(name);
}

// ---------- the connection form ----------

export type BackendSpec = {
  label: string;
  fields: Record<string, string>;
  secrets: string[];
  oauth?: string | null;
};

export type ConnForm = {
  name: string;
  params: Record<string, string>;
  /** undefined: unchanged (editing) · "": remove · text: set */
  secrets: Record<string, string | undefined>;
  /** SFTP signs in with a password or a private key. */
  sftpAuth: "pass" | "key_pem";
  /** What was pasted from `rclone authorize` (OAuth types). */
  tokenText: string;
};

export function emptyForm(spec: BackendSpec, source?: Pick<Source, "name" | "params" | "secrets"> | null): ConnForm {
  const params: Record<string, string> = {};
  for (const [k, def] of Object.entries(spec.fields))
    params[k] = str((source?.params as Record<string, unknown> | undefined)?.[k] ?? def);
  const keySet = Boolean(source?.secrets?.key_pem?.set);
  return {
    name: source?.name ?? "",
    params,
    secrets: {},
    sftpAuth: keySet && !source?.secrets?.pass?.set ? "key_pem" : "pass",
    tokenText: "",
  };
}

/** A name for a new connection, from what was typed ("SFTP · calls-gw", "S3 · eu-central-1", "Dropbox"). */
export function suggestName(type: SourceType, params: Record<string, string>): string {
  const host = (params.host || params.url || "").replace(/^(https?|webcals?):\/\//i, "").split(/[/:]/)[0];
  const extra =
    type === "s3" ? [params.provider !== "AWS" ? params.provider : "", params.region].filter(Boolean).join(" ") : host;
  return extra
    ? `${TYPE_NAME[type].replace(" / compatible", "")} · ${extra}`
    : TYPE_NAME[type] === "This machine"
      ? "Folder on this machine"
      : TYPE_NAME[type];
}

/** Problems that stop the test, by field; empty when the form can be sent. */
export function validateForm(
  type: SourceType,
  spec: BackendSpec,
  form: ConnForm,
  editing: boolean,
  isSet: (k: string) => boolean = () => false,
): Record<string, string> {
  const e: Record<string, string> = {};
  const need = (k: string, msg: string) => {
    if (!str(form.params[k])) e[k] = msg;
  };
  const secret = (k: string) => (form.secrets[k] === undefined ? editing && isSet(k) : Boolean(form.secrets[k]));
  if (spec.oauth) {
    const t = parseRcloneToken(form.tokenText);
    if (t.ok === false) e.token = t.error;
    else if (t.ok === null && !(editing && isSet("token"))) e.token = "Paste the token rclone printed.";
  }
  if (type === "s3") {
    need("access_key_id", "Enter the access key ID.");
    if (!secret("secret_access_key")) e.secret_access_key = "Enter the secret access key.";
    if (form.params.provider && form.params.provider !== "AWS" && !str(form.params.endpoint))
      e.endpoint = "Enter the endpoint for this provider.";
  }
  if (type === "sftp") {
    need("host", "Enter the host name.");
    need("user", "Enter the user name.");
    if (form.params.port && !/^\d{1,5}$/.test(form.params.port.trim())) e.port = "A port is a number, e.g. 22.";
    if (!secret(form.sftpAuth))
      e[form.sftpAuth] = form.sftpAuth === "pass" ? "Enter the password." : "Paste the private key.";
  }
  if (type === "smb") need("host", "Enter the host name.");
  if (type === "webdav") {
    if (!/^https?:\/\/\S+$/.test(str(form.params.url))) e.url = "Enter the full URL, starting with https://";
  }
  if (type === "imap") {
    need("host", "Enter the mail server’s host name.");
    need("user", "Enter the user name (often the email address).");
    if (form.params.port && !/^\d{1,5}$/.test(form.params.port.trim())) e.port = "A port is a number, e.g. 993.";
    if (!secret("pass")) e.pass = "Enter the password (an app password, where the provider has them).";
  }
  if (type === "ical") {
    const url = form.secrets.url;
    if (url === undefined ? !(editing && isSet("url")) : !/^(https?|webcals?):\/\/\S+$/i.test(url.trim()))
      e.url = "Enter the calendar’s address, starting with https:// or webcal://";
  }
  return e;
}

/** The request body parts: every parameter the type has, and only the secrets that change. */
export function buildPayload(
  spec: BackendSpec,
  form: ConnForm,
  type: SourceType,
): { params: Record<string, string>; secrets: Record<string, string | null> } {
  const params: Record<string, string> = {};
  for (const k of Object.keys(spec.fields)) params[k] = str(form.params[k]);
  const secrets: Record<string, string | null> = {};
  for (const k of spec.secrets) {
    if (k === "token") continue;
    const v = form.secrets[k];
    if (type === "sftp" && k !== form.sftpAuth) {
      // Signing in one way drops the other secret only when it was explicitly cleared.
      if (v === "") secrets[k] = null;
      continue;
    }
    if (v === undefined) continue;
    secrets[k] = v === "" ? null : type === "sftp" && k === "key_pem" ? v.replace(/\r\n/g, "\n") : v;
  }
  if (spec.oauth) {
    const t = parseRcloneToken(form.tokenText);
    if (t.ok) secrets.token = t.token;
  }
  return { params, secrets };
}
