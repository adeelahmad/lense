/**
 * A resource's files (the Files tab): its primary file and supplementary transcripts, captions, translations,
 * indexes, thumbnails and attachments. What each role takes, a role guessed from a file's name, and the words for a
 * file's row. The server checks the same rules; these say so before a file is sent.
 */
import type { FileLine, ResourceFile, SearchHit } from "@/app/openapi-client/types.gen";
import { languageName } from "@/components/library/model";
import { recordingHref } from "@/components/search/links";
import { bytes, plural, tc } from "@/lib/format";

export type FileRole = ResourceFile["role"];

const TEXT = [".txt", ".text", ".md", ".markdown", ".mdx", ".json", ".jsonl", ".srt", ".vtt", ".docx", ".doc", ".pdf"];
const IMAGES = [".jpg", ".jpeg", ".png", ".webp", ".gif"];

export type RoleInfo = {
  value: FileRole;
  label: string;
  /** The file types it takes (null: any). */
  accepts: string[] | null;
  /** What becomes of it. */
  hint: string;
};

export const ROLES: RoleInfo[] = [
  {
    value: "transcript",
    label: "Transcript",
    accepts: TEXT,
    hint: "Another transcript, such as one made by hand. Its lines are searchable.",
  },
  {
    value: "captions",
    label: "Captions",
    accepts: [".vtt", ".srt"],
    hint: "Timed captions or subtitles. Searchable, and published as WebVTT in IIIF.",
  },
  {
    value: "translation",
    label: "Translation",
    accepts: TEXT,
    hint: "The transcript in another language. Its lines are searchable.",
  },
  {
    value: "index",
    label: "Index",
    accepts: [...TEXT, ".xml"],
    hint: "A table of contents: WebVTT chapters, JSON, OHMS XML, or lines that start with a time.",
  },
  {
    value: "thumbnail",
    label: "Thumbnail",
    accepts: IMAGES,
    hint: "A picture that stands for the resource, in IIIF.",
  },
  {
    value: "attachment",
    label: "Attachment",
    accepts: null,
    hint: "Anything else, such as a release form. Only people with access to the resource can download it.",
  },
];

export const ROLE_LABEL = Object.fromEntries(ROLES.map((r) => [r.value, r.label])) as Record<FileRole, string>;
/** Roles whose files are read into lines. */
export const READ: FileRole[] = ["transcript", "captions", "translation", "index"];

export function extension(name: string): string {
  const m = /\.[^./\\]+$/.exec(name.trim());
  return m ? m[0].toLowerCase() : "";
}

/** The role a file probably has, from its name. */
export function guessRole(name: string): FileRole {
  const ext = extension(name);
  if (ext === ".vtt" || ext === ".srt") return "captions";
  if (IMAGES.includes(ext)) return "thumbnail";
  if (ext === ".xml") return "index";
  if (/\b(index|toc|chapters?|contents)\b/i.test(name) && TEXT.includes(ext)) return "index";
  if (TEXT.includes(ext) && ext !== ".pdf" && ext !== ".doc" && ext !== ".docx") return "transcript";
  return "attachment";
}

/** The language tag a file's name carries before its extension ("talk.pt-BR.vtt" → "pt-BR", "notes.en.srt" →
 * "en"), or "" when it has none. */
export function guessLanguage(name: string): string {
  const m = /\.([a-z]{2})(?:[-_]([A-Za-z]{2}|\d{3}))?\.[A-Za-z0-9]+$/.exec(name);
  if (!m) return "";
  return m[2] ? `${m[1]}-${m[2].length === 2 ? m[2].toUpperCase() : m[2]}` : m[1];
}

/** Why a file can't have this role, or null. */
export function typeProblem(role: FileRole, name: string): string | null {
  const info = ROLES.find((r) => r.value === role);
  if (!info?.accepts) return null;
  const ext = extension(name);
  return info.accepts.includes(ext)
    ? null
    : `${info.label} files are ${[...info.accepts].sort().join(" ")}; this is ${ext || "a file without an extension"}.`;
}

/** Why a file is too big to add, or null. */
export function sizeProblem(size: number, maxMb: number): string | null {
  if (size === 0) return "The file is empty.";
  return size > maxMb * 1024 * 1024 ? `Files can be up to ${maxMb} MB; this one is ${bytes(size)}.` : null;
}

/** A file's name in lists: its label, or its file name. */
export function fileTitle(f: Pick<ResourceFile, "label" | "name">): string {
  return f.label || f.name;
}

/** "Captions · English · 2 KB · 2 lines": what a file is, at a glance. */
export function fileMeta(f: ResourceFile): string {
  const lines =
    f.lines == null ? null : f.timed ? plural(f.lines, "timed line") : `${plural(f.lines, "line")}, no times`;
  return [ROLE_LABEL[f.role], f.language ? languageName(f.language) : null, bytes(f.size), lines]
    .filter(Boolean)
    .join(" · ");
}

/** "Added by Ed Editor": who added it (the name they gave, else their email). */
export function addedBy(f: Pick<ResourceFile, "created_by" | "created_by_name">): string | null {
  const who = f.created_by_name || f.created_by;
  return who ? `Added by ${who}` : null;
}

/** Where a file's lines open on the resource's page; a line, when given. */
export function fileHref(rid: number, fid: number, line?: number | null): string {
  return `/resources/${rid}?file=${fid}${line != null ? `&line=${line}` : ""}`;
}

/** Where a search hit opens: its moment, or for a line of a file without times, the line in the file. */
export function hitHref(
  hit: Pick<SearchHit, "recording_id" | "t0" | "file" | "line"> & { page?: number | null },
): string {
  if (hit.page != null) return `/resources/${hit.recording_id}?page=${hit.page + 1}`; // a document's text: its page
  if (hit.t0 == null && hit.file != null) return fileHref(hit.recording_id, hit.file, hit.line);
  return recordingHref(hit.recording_id, hit.t0);
}

/** A line's time ("1:05" or "1:05–2:10"), or null without one. */
export function lineTime(l: Pick<FileLine, "t0" | "t1">): string | null {
  if (l.t0 == null) return null;
  return l.t1 != null && l.t1 > l.t0 ? `${tc(l.t0)}–${tc(l.t1)}` : tc(l.t0);
}

/** The question before deleting a file. */
export function deleteQuestion(f: ResourceFile): string {
  return f.lines
    ? `Delete ${fileTitle(f)}? Its ${plural(f.lines, "line")} leave search too.`
    : `Delete ${fileTitle(f)}?`;
}

/** The changes to send for a file's details: only what differs; empty text clears a field. */
export function detailChanges(
  f: Pick<ResourceFile, "role" | "label" | "language" | "description">,
  next: { role: FileRole; label: string; language: string; description: string },
): Partial<{ role: FileRole; label: string | null; language: string | null; description: string | null }> {
  const out: Partial<{ role: FileRole; label: string | null; language: string | null; description: string | null }> =
    {};
  if (next.role !== f.role) out.role = next.role;
  for (const k of ["label", "language", "description"] as const) {
    const v = next[k].trim() || null;
    if (v !== (f[k] ?? null)) out[k] = v;
  }
  return out;
}

const LANG_RX = /^[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,8})*$/;
/** Why a language code won't do, or null (empty is fine: not known). */
export function languageProblem(code: string): string | null {
  const v = code.trim();
  return !v || LANG_RX.test(v) ? null : "Use a language code such as en or pt-BR.";
}

/** `?file=<id>&line=<n>` from a page's address: the file to show, and a line of it. */
export function parseFileFocus(
  file: string | string[] | undefined,
  line: string | string[] | undefined,
): { file: number; line: number | null } | null {
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const f = Number(one(file));
  if (!Number.isInteger(f) || f <= 0) return null;
  const l = Number(one(line));
  return { file: f, line: one(line) != null && Number.isInteger(l) && l >= 0 ? l : null };
}
