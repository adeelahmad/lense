import type { RecordingSummary } from "@/app/openapi-client/types.gen";

/** A link to a moment: the recording page, seeking to `ms` (as whole seconds, `?t=`). */
export function recordingHref(id: number, ms?: number | null): string {
  return ms && ms > 0 ? `/resources/${id}?t=${Math.floor(ms / 1000)}` : `/resources/${id}`;
}

/** True when a recording has media to play (imported transcripts have none); undefined when unknown. */
export function hasMedia(r: Pick<RecordingSummary, "source" | "media_kind"> | undefined): boolean | undefined {
  if (!r) return undefined;
  return r.source === "audio" || r.media_kind === "audio" || r.media_kind === "video";
}
