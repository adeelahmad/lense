/**
 * Import: what each dropped file is, whether it can be imported here, and the speaker mapping text
 * ("SPEAKER_00 = Host A", one per line) that becomes the API's `speakers` string ("SPEAKER_00=Host A,...").
 * Pure functions, tested in __tests__/import-files.test.ts.
 */
import type { UploadLimits } from "@/app/openapi-client/types.gen";
import { bytes } from "@/lib/format";

/** Transcript types the importer reads (the backend's IMPORT_EXT). */
export const TRANSCRIPT_EXT = [
  ".txt",
  ".md",
  ".markdown",
  ".mdx",
  ".docx",
  ".doc",
  ".pdf",
  ".json",
  ".jsonl",
  ".srt",
  ".vtt",
];
const AUDIO_EXT = [".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma", ".aif", ".aiff", ".amr", ".weba"];
const VIDEO_EXT = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".3gp"];
/** Documents and images (the backend's DOCUMENT_EXT and IMAGE_EXT): uploaded in pieces, their pages drawn and read.
 * Documents other than PDFs are made into PDFs on the server first: Office and OpenDocument files by LibreOffice;
 * text, Markdown, saved web pages and emails by Chromium (or LibreOffice). */
const OFFICE_EXT = [".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx", ".odp", ".xls", ".xlsx", ".ods"];
const TEXT_DOC_EXT = [".txt", ".text", ".md", ".markdown", ".mdx"];
const PAGE_EXT = [".html", ".htm"];
const EMAIL_EXT = [".eml", ".msg"];
export const DOCUMENT_EXT = [".pdf", ...OFFICE_EXT, ...TEXT_DOC_EXT, ...PAGE_EXT, ...EMAIL_EXT];
export const IMAGE_EXT = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp"];
/** What a document is, in words (the backend's convert.WORDS). */
const DOCUMENT_NAME: Record<string, string> = {
  ".pdf": "PDF document",
  ".doc": "Word document",
  ".docx": "Word document",
  ".odt": "OpenDocument text",
  ".rtf": "RTF document",
  ".ppt": "PowerPoint presentation",
  ".pptx": "PowerPoint presentation",
  ".odp": "OpenDocument presentation",
  ".xls": "Excel spreadsheet",
  ".xlsx": "Excel spreadsheet",
  ".ods": "OpenDocument spreadsheet",
  ".txt": "Text document",
  ".text": "Text document",
  ".md": "Markdown document",
  ".markdown": "Markdown document",
  ".mdx": "MDX document",
  ".html": "Web page",
  ".htm": "Web page",
  ".eml": "Email",
  ".msg": "Outlook email",
};

export const SUPPORTED_LIST = "txt, md, mdx, docx, doc, pdf, srt, vtt, json, jsonl";

/** What the server accepts until GET /uploads/limits answers: the defaults of Settings → Uploads and
 * server.max_upload_mb. */
export const DEFAULT_LIMITS: UploadLimits = {
  max_mb: 4096,
  extensions: [
    ".aac",
    ".amr",
    ".avi",
    ".bmp",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".png",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
    ".webp",
    ...DOCUMENT_EXT,
  ].sort(),
  chunk_mb: 8,
  transcript_mb: 50,
  convert: { office: true, pages: true, msg: false },
};

export type FileKind = "transcript" | "audio" | "video" | "document" | "image" | "unsupported";

export function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i).toLowerCase() : "";
}

export function stemOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(0, i) : name;
}

/** What a file becomes: a document (PDFs, Office files, text, Markdown, web pages, emails; unless someone chooses a
 * transcript, see canBeTranscript), an image, a transcript (subtitles and JSON), audio or video. */
export function kindOf(name: string): FileKind {
  const ext = extOf(name);
  if (DOCUMENT_EXT.includes(ext)) return "document";
  if (IMAGE_EXT.includes(ext)) return "image";
  if (TRANSCRIPT_EXT.includes(ext)) return "transcript";
  if (AUDIO_EXT.includes(ext)) return "audio";
  if (VIDEO_EXT.includes(ext)) return "video";
  return "unsupported";
}

/** Files that go up in pieces as they are (audio, video, documents and images); transcripts are read first. */
export function isUpload(kind: FileKind): boolean {
  return kind === "audio" || kind === "video" || kind === "document" || kind === "image";
}

/** The audio and video types the server takes (for a picker of media only). */
export function mediaTypes(limits: UploadLimits): string {
  return limits.extensions.filter((e) => AUDIO_EXT.includes(e) || VIDEO_EXT.includes(e)).join(",");
}

/** A document that can be imported as a transcript instead (its text only): a PDF, a Word, text or Markdown file. */
export function canBeTranscript(name: string): boolean {
  const ext = extOf(name);
  return DOCUMENT_EXT.includes(ext) && TRANSCRIPT_EXT.includes(ext);
}

/** Why this server can't read a document (null when it can, or for a PDF): what it would need. */
export function conversionProblem(name: string, limits: UploadLimits = DEFAULT_LIMITS): string | null {
  const ext = extOf(name);
  const can = limits.convert;
  if (!DOCUMENT_EXT.includes(ext) || ext === ".pdf" || !can) return null;
  if (OFFICE_EXT.includes(ext))
    return can.office ? null : "Reading it needs LibreOffice on the server (the lens:full image).";
  if (ext === ".msg") return can.msg ? null : "Reading Outlook emails needs the extract-msg package on the server.";
  return can.pages ? null : "Reading it needs Chromium or LibreOffice on the server (the lens:full image).";
}

/** What a file becomes unless someone chooses otherwise: a document where this server can read it as one, else the
 * transcript it can be. */
export function defaultKind(name: string, limits: UploadLimits = DEFAULT_LIMITS): FileKind {
  const kind = kindOf(name);
  return kind === "document" && conversionProblem(name, limits) && canBeTranscript(name) ? "transcript" : kind;
}

/** What a file to upload is, in words: "Video", "Audio", "PDF document", "Word document", "Email", "Image". */
export function uploadKindName(kind: FileKind, name?: string): string {
  if (kind === "document") return DOCUMENT_NAME[extOf(name ?? "")] ?? "Document";
  return kind === "video" ? "Video" : kind === "image" ? "Image" : "Audio";
}

const FORMAT_NAME: Record<string, string> = {
  srt: "SubRip (.srt)",
  vtt: "WebVTT (.vtt)",
  json: "JSON",
  jsonl: "JSON lines",
  text: "Plain text",
  txt: "Plain text",
  markdown: "Markdown",
  md: "Markdown",
  mdx: "MDX",
  docx: "Word (.docx)",
  doc: "Word (.doc)",
  pdf: "PDF",
};

/** The parser's format word as people say it: "SubRip (.srt)", "Plain text". */
export function formatName(format: string | null | undefined): string {
  const f = (format || "").toLowerCase().replace(/^\./, "");
  return FORMAT_NAME[f] ?? (f ? f.toUpperCase() : "Unknown");
}

/** Formats without timings: turn times are estimated from word count ("~0:04"). */
export function isUntimed(format: string | null | undefined): boolean {
  return ["text", "txt", "markdown", "md", "mdx", "docx", "doc", "pdf"].includes((format || "").toLowerCase());
}

/** A friendly title from a file name: "ep14-transcript.srt" → "ep14 transcript". */
export function titleFromName(name: string): string {
  return stemOf(name).replace(/[_]+/g, " ").replace(/\s+/g, " ").trim() || name;
}

export type Problem = {
  code: "unsupported" | "too-large" | "unreadable" | "empty";
  title: string;
  body: string;
};

const MB = 1024 * 1024;

/** Why a file can't be imported before we even send it (as `kind`: what it is, or the transcript it's read as); null
 * when it can be tried. */
export function localProblem(
  file: { name: string; size: number },
  limits: UploadLimits = DEFAULT_LIMITS,
  kind: FileKind = kindOf(file.name),
): Problem | null {
  const ext = extOf(file.name);
  if (isUpload(kind)) {
    const convert = kind === "document" ? conversionProblem(file.name, limits) : null;
    if (convert)
      return {
        code: "unsupported",
        title: `This server can’t read ${uploadKindName(kind, file.name).toLowerCase()}s`,
        body: canBeTranscript(file.name) ? `${convert} Import it as a transcript instead.` : convert,
      };
    if (!limits.extensions.includes(ext))
      return {
        code: "unsupported",
        title: `${ext.slice(1).toUpperCase()} files can’t be uploaded here`,
        body: `This server takes ${limits.extensions.map((e) => e.slice(1)).join(", ")}. Convert it to one of those, or ask an admin to allow ${ext} files in Settings → Uploads.`,
      };
    if (file.size > limits.max_mb * MB)
      return {
        code: "too-large",
        title: `Too large to upload here (limit ${bytes(limits.max_mb * MB)})`,
        body:
          kind === "audio" || kind === "video"
            ? "Put it in a watched folder instead — sources have no size limit — or compress it to m4a or opus."
            : "Split it into smaller files, or ask an admin to raise the limit in Settings → Uploads.",
      };
    return null;
  }
  if (kind === "unsupported") {
    return {
      code: "unsupported",
      title: `${ext ? ext.slice(1).toUpperCase() : "These"} files can’t be imported`,
      body: `Save it as .docx, .pdf or plain text and drop it again. Supported: documents (PDF, Office, text, web pages, emails), images, audio, video, and transcripts (${SUPPORTED_LIST}).`,
    };
  }
  if (file.size > limits.transcript_mb * MB) {
    return {
      code: "too-large",
      title: `Too large to upload here (limit ${limits.transcript_mb} MB)`,
      body: "Put it in a watched folder instead — sources have no size limit — or split it into smaller files.",
    };
  }
  return null;
}

/** A file as `kind`: uploads (audio, video, documents, images) are ready as they are; transcripts are read first (the
 * preview); a file that can't be imported is blocked, with why. */
export function asKind(
  file: { name: string; size: number },
  kind: FileKind,
  limits: UploadLimits,
): { kind: FileKind; status: "ready" | "reading" | "blocked"; problem?: Problem; preview: undefined } {
  const problem = localProblem(file, limits, kind);
  return {
    kind,
    status: problem ? "blocked" : isUpload(kind) ? "ready" : "reading",
    problem: problem ?? undefined,
    preview: undefined,
  };
}

/** What the server said when it couldn't read a transcript, as a problem card. */
export function readProblem(message: string, name: string): Problem {
  const m = message.replace(/^could not read that transcript:\s*/i, "");
  if (/no transcript text found|nothing to import/i.test(m) || /no text/i.test(m)) {
    return extOf(name) === ".pdf"
      ? {
          code: "empty",
          title: "This PDF has no text to import",
          body: "It may be a scan. Import it as a document instead: its pages are kept and read by OCR.",
        }
      : {
          code: "empty",
          title: "No transcript text found",
          body: "The file is empty, or its lines aren’t in a shape the importer knows. Try pasting it instead to see how it’s read.",
        };
  }
  if (/files up to/i.test(m))
    return {
      code: "too-large",
      title: "Too large to upload here",
      body: `${m[0].toUpperCase()}${m.slice(1)}. Put it in a watched folder instead — sources have no size limit.`,
    };
  return {
    code: "unreadable",
    title: "We couldn’t read this transcript",
    body: `${m[0]?.toUpperCase() ?? ""}${m.slice(1)}`,
  };
}

// ---------- speaker mapping ----------

/** Labels a diarizer or subtitle file makes up; the backend gives each its own unnamed speaker. */
export const GENERIC_LABEL = /^(?:SPEAKER_?\d+|spk_?\d+|S\d+|CH\d+|Speaker \d+|unknown)$/i;

export type MappingLine = { label: string; name: string; line: number };
export type Mapping = {
  pairs: MappingLine[];
  errors: string[];
  warnings: string[];
};

/** One line per detected label, mapped to itself: people edit the right-hand side. */
export function initialMapping(labels: string[]): string {
  return labels.map((l) => `${l} = ${l}`).join("\n");
}

/** Parse "label = speaker" lines. Names can't hold "," or "=" (the API joins pairs with commas). */
export function parseMapping(text: string, known: string[] = []): Mapping {
  const pairs: MappingLine[] = [];
  const errors: string[] = [];
  const warnings: string[] = [];
  const seen = new Set<string>();
  const knownSet = new Set(known);
  text.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const eq = line.indexOf("=");
    if (eq < 0) {
      errors.push(`Line ${i + 1}: write it as “label = speaker”.`);
      return;
    }
    const label = line.slice(0, eq).trim();
    const name = line.slice(eq + 1).trim();
    if (!label) {
      errors.push(`Line ${i + 1}: the label before “=” is missing.`);
      return;
    }
    if (/[=,]/.test(name)) {
      errors.push(`Line ${i + 1}: a speaker name can’t contain “=” or “,”.`);
      return;
    }
    if (label.includes(",")) {
      errors.push(`Line ${i + 1}: a label can’t contain “,”.`);
      return;
    }
    if (seen.has(label)) {
      errors.push(`Line ${i + 1}: “${label}” is mapped twice.`);
      return;
    }
    seen.add(label);
    if (known.length && !knownSet.has(label))
      warnings.push(`“${label}” isn’t a label in this transcript; that line does nothing.`);
    pairs.push({ label, name: name || label, line: i + 1 });
  });
  return { pairs, errors, warnings };
}

/** The API's `speakers` string: only the labels that get a different name ("S1=Alice,S2=Bob"), or null. */
export function mappingParam(m: Mapping): string | null {
  const out = m.pairs.filter((p) => p.name && p.name !== p.label).map((p) => `${p.label}=${p.name}`);
  return out.length ? out.join(",") : null;
}

/** Each label's final display name (unmapped labels keep their own). */
export function mappedNames(labels: string[], m: Mapping): Map<string, string> {
  const by = new Map(m.pairs.map((p) => [p.label, p.name]));
  return new Map(labels.map((l) => [l, by.get(l) || l]));
}

/** Read a file as base64 (without the data: prefix), for the JSON import API. */
export function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(r.error ?? new Error("Couldn’t read the file"));
    r.onload = () => {
      const s = String(r.result ?? "");
      resolve(s.slice(s.indexOf(",") + 1));
    };
    r.readAsDataURL(file);
  });
}

/** "Then run": the namespace's own pipeline (value "", the default), then every saved pipeline. */
export function pipelineOptions(
  namespaceDefault: string,
  pipelines: { id: number; name: string }[],
): { value: string; label: string }[] {
  return [
    { value: "", label: `${namespaceDefault} (the namespace’s)` },
    ...pipelines.map((p) => ({ value: String(p.id), label: p.name })),
  ];
}

/** Files of a source that can be imported: audio, video, documents, images and transcripts (not folders or others). */
export function importable(entry: { name: string; dir: boolean }): boolean {
  return !entry.dir && !entry.name.startsWith(".") && kindOf(entry.name) !== "unsupported";
}

/** What importing chosen files of a source did, in one line: "2 imported · 1 already here · 1 skipped". */
export function sourceImportSummary(results: { status: string }[]): string {
  const n = (s: string) => results.filter((r) => r.status === s).length;
  return [
    n("queued") ? `${n("queued")} imported` : null,
    n("already") ? `${n("already")} already here` : null,
    n("skipped") ? `${n("skipped")} skipped` : null,
    n("error") ? `${n("error")} failed` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function namespaceNameProblem(name: string): string | null {
  if (!name) return "Choose a namespace.";
  return /^[a-z0-9][a-z0-9_-]{0,40}$/.test(name)
    ? null
    : "Use lowercase letters, digits, - and _ (up to 41 characters), starting with a letter or digit.";
}
