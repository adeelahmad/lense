import type { BatchProgress, BatchSelection, Estimate } from "@/app/openapi-client/types.gen";
import { count, plural } from "@/lib/format";

/** "~40 s", "~28 min", "~2.5 h". */
export function approxDuration(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(seconds ?? 0));
  if (s < 60) return `~${s} s`;
  if (s < 3600) return `~${Math.round(s / 60)} min`;
  const h = s / 3600;
  return `~${h < 10 ? h.toFixed(1).replace(/\.0$/, "") : Math.round(h)} h`;
}

/** "~800", "~270k", "~1.2M". */
export function approxCount(n: number | null | undefined): string {
  const v = Math.max(0, n ?? 0);
  if (v < 1000) return `~${Math.round(v)}`;
  if (v < 1e6) return `~${Math.round(v / 1000)}k`;
  return `~${(v / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
}

export function money(cost: number | null | undefined): string {
  return cost == null ? "—" : `$${cost.toFixed(2)}`;
}

/** The typed text a big run asks for ("RUN 214"): case and spacing don't matter. */
export function confirmMatches(typed: string, expected: string | null | undefined): boolean {
  if (!expected) return true;
  const norm = (s: string) => s.trim().replace(/\s+/g, " ").toUpperCase();
  return norm(typed) === norm(expected);
}

/** The number in a confirm text ("RUN 214" → "214"). */
export function confirmNumber(expected: string | null | undefined): string {
  return /\d+/.exec(expected ?? "")?.[0] ?? "";
}

/** What a run would need that a transcript-only recording can't give: audio and video steps. */
export const MEDIA_STEPS = new Set(["transcribe", "diarize", "shots", "ocr", "faces", "objects"]);

/** True when every recording is transcript-only and every step needs media: nothing can run. */
export function nothingRunnable(est: Pick<Estimate, "by_kind" | "recordings">, steps: { type?: unknown }[]): boolean {
  if (!est.recordings) return true;
  const kinds = est.by_kind ?? {};
  const onlyTranscripts = Object.keys(kinds).length > 0 && Object.keys(kinds).every((k) => k === "transcript");
  const types = steps.map((s) => String(s.type ?? ""));
  return onlyTranscripts && types.length > 0 && types.every((t) => MEDIA_STEPS.has(t));
}

/** Where a run's recordings come from, in words. */
export function describeSelection(
  sel: BatchSelection | Record<string, unknown> | null | undefined,
  names: { collection?: string; entity?: string; speaker?: string } = {},
): string {
  const s = (sel ?? {}) as BatchSelection;
  if (s.collection) return `the saved collection ${names.collection ? `“${names.collection}”` : `#${s.collection}`}`;
  if (s.recordings?.length) return plural(s.recordings.length, "chosen recording");
  const parts: string[] = [];
  const f = (s.filter ?? {}) as Record<string, unknown>;
  if (s.entity || (f.entities as unknown[] | undefined)?.length)
    parts.push(`every recording that mentions ${names.entity ?? "the entity"}`);
  if (s.speaker || (f.speakers as unknown[] | undefined)?.length)
    parts.push(`every recording with ${names.speaker ?? "the speaker"}`);
  if (typeof f.q === "string" && f.q) parts.push(`recordings matching “${f.q}”`);
  const ns = s.namespace ?? (Array.isArray(f.namespaces) ? (f.namespaces as string[]).join(", ") : null);
  if (ns) parts.push(parts.length ? `in ${ns}` : `every recording in ${ns}`);
  return parts.join(" ") || "every recording you can change";
}

export type ProgressParts = {
  done: number;
  failed: number;
  running: number;
  queued: number;
  total: number;
};

/** Progress counts from a batch's job counts. */
export function progressParts(p: BatchProgress | null | undefined): ProgressParts {
  const c = p?.counts ?? {};
  const total = p?.total ?? 0;
  const done = (c.succeeded ?? 0) + (c.cancelled ?? 0);
  const failed = c.failed ?? 0;
  const running = c.running ?? 0;
  return {
    done,
    failed,
    running,
    queued: Math.max(0, total - done - failed - running),
    total,
  };
}

/** A rough time left from the average pace so far. */
export function eta(parts: ProgressParts, startedAt: string | null | undefined, now = Date.now()): number | null {
  const finished = parts.done + parts.failed;
  const left = parts.total - finished;
  if (!startedAt || finished < 1 || left < 1) return null;
  const elapsed = (now - Date.parse(startedAt)) / 1000;
  if (!Number.isFinite(elapsed) || elapsed <= 0) return null;
  return (elapsed / finished) * left;
}

/** "24 of 39". */
export function progressLabel(parts: ProgressParts): string {
  return `${count(parts.done + parts.failed)} of ${count(parts.total)}`;
}

/** Status words for a batch (backend: running, sample, sample done, paused, cancelled, finished). */
export function batchTone(status: string): "intent" | "green" | "red" | "gate" | "neutral" {
  if (status === "finished") return "green";
  if (status === "sample done") return "gate";
  if (status === "cancelled") return "neutral";
  if (status === "paused") return "neutral";
  return "intent";
}
