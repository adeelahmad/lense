/**
 * Blur faces for sharing, in a video's People tab (docs/video.md, Blurring faces for sharing): what to ask for, and
 * one line on the job making the copy. Pure functions.
 */
import type { FaceBlur, FaceBlurCopy, FaceBlurCreate } from "@/app/openapi-client/types.gen";
import { bytes, plural, relative } from "@/lib/format";

const ACTIVE = ["queued", "running", "held"];

/** What to ask for: every face when all are ticked, else the ones ticked, in the order they first appear. */
export function blurRequest(picked: ReadonlySet<string>, all: readonly string[]): FaceBlurCreate {
  return all.every((id) => picked.has(id))
    ? { who: "all" }
    : { who: "chosen", tracks: all.filter((id) => picked.has(id)) };
}

/** Whether the latest job is still to finish (the tab asks again while it is). */
export function blurring(s: FaceBlur | null | undefined): boolean {
  return ACTIVE.includes(s?.job?.status ?? "");
}

export type BlurStatus = { text: string; tone: "intent" | "gate" | "red"; progress: number | null };

/** Where the latest job is: blurring (and how far), waiting (and why), or failed (and why); null when it's done. */
export function blurStatus(s: FaceBlur | null | undefined): BlurStatus | null {
  const j = s?.job;
  if (!j) return null;
  if (j.status === "running")
    return {
      text: j.progress != null ? `Blurring faces… ${j.progress}%` : "Blurring faces…",
      tone: "intent",
      progress: j.progress != null ? j.progress / 100 : null,
    };
  if (j.status === "queued" || j.status === "held")
    return { text: j.waits ? `Waiting to start: ${j.waits}` : "Waiting to start…", tone: "gate", progress: null };
  if (j.status === "failed")
    return { text: j.error ? `Failed: ${j.error}` : "Couldn’t blur the faces", tone: "red", progress: null };
  return null;
}

/** "2 faces blurred · 1.2 MB · 5 min ago": what the copy is. */
export function copyLine(c: FaceBlurCopy, now = Date.now()): string {
  return [
    plural(c.faces, "face") + " blurred",
    c.size != null ? bytes(c.size) : null,
    c.made_at ? relative(c.made_at, now) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
