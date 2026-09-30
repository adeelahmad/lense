/**
 * Reading a Presentation 3 Manifest for the IIIF panel (what's published) and IIIF Content State links (moments).
 */
import { hours, tc } from "@/lib/format";

/** A namespace's running time: "12 min" under an hour, then hours ("1.4 h", "36 h"). */
export function runtime(ms: number | null | undefined): string {
  const m = (ms ?? 0) / 60_000;
  if (m <= 0) return "0 min";
  if (m < 1) return "under 1 min";
  return m < 60 ? `${Math.round(m)} min` : hours(ms);
}

const EXT: Record<string, string> = {
  "audio/mpeg": "mp3",
  "audio/mp4": "m4a",
  "audio/x-m4a": "m4a",
  "audio/aac": "aac",
  "audio/ogg": "ogg",
  "audio/opus": "opus",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
  "audio/flac": "flac",
  "audio/webm": "webm",
  "video/mp4": "mp4",
  "video/webm": "webm",
  "video/quicktime": "mov",
  "text/vtt": "vtt",
  "application/x-subrip": "srt",
  "text/plain": "txt",
  "text/markdown": "md",
  "application/json": "json",
};

/** "audio/mp4" → "m4a". */
export function formatExt(format: string | null | undefined): string {
  const f = (format ?? "").split(";")[0].trim().toLowerCase();
  return EXT[f] ?? (f.split("/")[1] || "file");
}

type Json = Record<string, unknown>;
const arr = (v: unknown): Json[] => (Array.isArray(v) ? (v as Json[]) : []);
const obj = (v: unknown): Json => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {});
const text = (lm: unknown): string => {
  for (const vals of Object.values(obj(lm))) if (Array.isArray(vals) && vals[0]) return String(vals[0]);
  return "";
};

export type Included = {
  key: string;
  label: string;
  detail: string;
  on: boolean;
  locked?: boolean;
};

/** What a Manifest publishes, row by row: media, captions, annotation layers, chapters, downloads, search, records. */
export function includedFrom(manifest: Json | null | undefined): Included[] {
  const m = obj(manifest);
  const canvas = arr(m.items)[0] ?? {};
  const body = obj(obj(arr(obj(arr(canvas.items)[0]).items)[0]).body);
  const pages = arr(canvas.annotations);
  const captions = obj(obj(arr(obj(pages[0]).items)[0]).body);
  const out: Included[] = [];
  if (body.id) {
    const kind = body.type === "Video" ? "Video" : "Audio";
    const dur = typeof body.duration === "number" ? tc(body.duration * 1000) : "";
    out.push({
      key: "media",
      label: kind,
      detail: [formatExt(body.format as string), dur].filter(Boolean).join(" · "),
      on: true,
      locked: Boolean(body.service),
    });
  } else
    out.push({
      key: "media",
      label: "Audio",
      detail: "no audio: transcript only",
      on: false,
    });
  if (captions.id)
    out.push({
      key: "captions",
      label: "Transcript captions",
      detail: ["WebVTT", captions.language as string].filter(Boolean).join(" · "),
      on: true,
      locked: Boolean(captions.service),
    });
  for (const p of pages.slice(1))
    out.push({
      key: String(p.id),
      label: text(p.label) || "Annotations",
      detail: "annotation layer",
      on: true,
    });
  const contents = arr(m.structures).find((r) => String(r.id ?? "").endsWith("/range/contents"));
  const shots = arr(m.structures).find((r) => String(r.id ?? "").endsWith("/range/shots"));
  out.push({
    key: "chapters",
    label: "Chapters",
    detail: contents ? `${arr(contents.items).length} as structures (table of contents)` : "none yet",
    on: Boolean(contents),
  });
  if (shots)
    out.push({
      key: "shots",
      label: "Shots",
      detail: `${arr(shots.items).length} as structures`,
      on: true,
    });
  const downloads = arr(m.rendering).map((r) => formatExt(r.format as string));
  out.push({
    key: "downloads",
    label: "Downloads",
    detail: downloads.length ? downloads.join(" · ") : "transcript not open",
    on: downloads.length > 0,
  });
  out.push({
    key: "search",
    label: "Search inside",
    detail: m.service ? "Content Search 2.0" : "transcript not open",
    on: Boolean(m.service),
  });
  const records = arr(m.seeAlso).map((r) => (String(r.format).includes("xml") ? "Dublin Core" : "schema.org"));
  out.push({
    key: "records",
    label: "Descriptive records",
    detail: records.join(" · ") || "none",
    on: records.length > 0,
  });
  return out;
}

/** "14:29" or "1:02:03" or "90" (seconds) → seconds; null when it isn't a time. */
export function parseClock(v: string): number | null {
  const s = v.trim();
  if (!s) return null;
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(s)) return null;
  const parts = s.split(":").map(Number);
  if (parts.slice(1).some((p) => p >= 60)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

/** "14:29–14:35" for a moment. */
export function momentLabel(t0: number, t1?: number | null): string {
  return t1 != null && t1 > t0 ? `${tc(t0 * 1000)}–${tc(t1 * 1000)}` : tc(t0 * 1000);
}

/** Decodes a IIIF Content State (base64url of the URI-encoded JSON); null when it isn't one. */
export function decodeContentState(token: string): Json | null {
  try {
    const b64 = token.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (token.length % 4)) % 4);
    const bin = typeof atob === "function" ? atob(b64) : Buffer.from(b64, "base64").toString("binary");
    return JSON.parse(decodeURIComponent(bin)) as Json;
  } catch {
    return null;
  }
}

/** Where a content state points in this archive: the recording and the start time (seconds). */
export function contentStateTarget(state: Json | null): { recording: number; t0?: number; t1?: number } | null {
  if (!state) return null;
  const target = obj(Array.isArray(state.target) ? state.target[0] : state.target);
  const id = String(target.id ?? state.id ?? "");
  const m = id.match(/\/iiif\/(\d+)\/(?:canvas|manifest)/);
  if (!m) return null;
  const t = id.match(/#t=([\d.]+)(?:,([\d.]+))?/);
  return {
    recording: Number(m[1]),
    t0: t ? Number(t[1]) : undefined,
    t1: t?.[2] ? Number(t[2]) : undefined,
  };
}

/** A schema error like "items/0/items: [] is too short" → where and what, for plain display. */
export function schemaProblem(p: unknown): { where: string; what: string } {
  const s = String(p);
  const i = s.indexOf(": ");
  return i > 0 ? { where: s.slice(0, i), what: s.slice(i + 2) } : { where: "", what: s };
}
