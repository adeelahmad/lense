/**
 * Uploading audio and video in pieces (docs/api.md, Uploads): which piece goes next, when to try a piece again and
 * how long to wait, and which unfinished upload a file carries on. Pure, so it's tested without a browser
 * (__tests__/upload-model.test.ts); components/import/uploader.ts does the sending.
 */
import type { Upload } from "@/app/openapi-client/types.gen";
import { bytes } from "@/lib/format";

const MB = 1024 * 1024;

/** The next piece to send, [start, end): from where the server has got to, `pieceMb` at a time. */
export function nextPiece(offset: number, size: number, pieceMb: number): [number, number] {
  return [offset, Math.min(size, offset + Math.max(1, pieceMb) * MB)];
}

/** How many pieces the whole file takes. */
export function pieceCount(size: number, pieceMb: number): number {
  return Math.max(1, Math.ceil(size / (Math.max(1, pieceMb) * MB)));
}

/** Whether a piece that failed is worth sending again: no connection, a timeout, a busy or failing server (but not a
 * full disk, which waiting won't fix). */
export function retryable(status: number): boolean {
  return status === 0 || status === 408 || status === 429 || (status >= 500 && status !== 507);
}

/** How long to wait before the nth try: 1, 2, 4, 8 and 16 seconds, then 30. */
export function retryDelay(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, attempt - 1));
}

/** The name the server keeps a file under (clean_name in app/domain/uploads.py): no folders, control characters or
 * leading dots, the extension in lowercase. */
export function storedName(name: string): string {
  const base = name.normalize("NFC").replace(/\\/g, "/").split("/").pop() ?? "";
  const n = base
    .replace(/[\p{C}\p{Zl}\p{Zp}<>:"|?*]/gu, "")
    .replace(/(?! )\p{Zs}/gu, "")
    .trim()
    .replace(/^[. ]+/, "");
  const dot = n.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [n.slice(0, dot), n.slice(dot)] : [n, ""];
  return (stem.trim() || "upload") + ext.toLowerCase();
}

/** An unfinished upload of this file into this namespace (as the audio of recording `attach`, when given), to carry
 * on rather than start again. */
export function resumeFrom<U extends Pick<Upload, "state" | "namespace" | "size" | "filename" | "attach">>(
  uploads: U[],
  file: { name: string; size: number },
  namespace: string,
  attach: number | null = null,
): U | null {
  const name = storedName(file.name);
  return (
    uploads.find(
      (u) =>
        u.state === "receiving" &&
        (attach != null || u.namespace === namespace) &&
        u.size === file.size &&
        u.filename === name &&
        (u.attach ?? null) === attach,
    ) ?? null
  );
}

/** "ep14-transcript.srt" and "ep14.m4a" go together: the name without its extension or a "-transcript" ending. */
export function twinKey(name: string): string {
  const dot = name.lastIndexOf(".");
  return (dot > 0 ? name.slice(0, dot) : name).toLowerCase().replace(/[-_ ]*(transcript|captions|subtitles|subs)$/, "");
}

/** Transcripts dropped with their audio: each transcript's id → the audio or video file with the same name, which
 * becomes its media instead of a recording of its own. Each file pairs once, the first match first. */
export function pairTwins(items: { id: string; name: string; media: boolean }[]): Map<string, string> {
  const pairs = new Map<string, string>();
  const taken = new Set<string>();
  for (const t of items) {
    if (t.media) continue;
    const m = items.find((x) => x.media && !taken.has(x.id) && twinKey(x.name) === twinKey(t.name));
    if (m) {
      pairs.set(t.id, m.id);
      taken.add(m.id);
    }
  }
  return pairs;
}

/** How far through the file: "36.0 MB of 80.0 MB". */
export function sentText(sent: number, size: number): string {
  return `${bytes(sent)} of ${bytes(size)}`;
}

/** The share of the file that has arrived, 0–1. */
export function sentShare(sent: number | undefined, size: number | undefined): number {
  return size ? Math.min(1, Math.max(0, (sent ?? 0) / size)) : 0;
}

/** What went wrong with an upload, for its row in the queue. */
export function uploadError(status: number, message: string): string {
  if (status === 404) return "This upload was cancelled or has expired. Try again to start it over.";
  if (status === 507) return "The server has no room for this file. Ask an admin to free some space.";
  if (status === 0) return "Can’t reach the server. Try again to carry on where it stopped.";
  return message ? message[0].toUpperCase() + message.slice(1) : "The upload failed.";
}
