/**
 * Lining an imported transcript up with its audio (docs/processing.md, Lining up a transcript with its audio): when
 * the page offers it, and what it says about the lines' times. Pure functions.
 */
import type { Recording } from "@/app/openapi-client/types.gen";
import { isActive, type JobInfo } from "@/components/recording/jobs";

type Times = Pick<Recording, "times" | "aligned">;

/** A job still to line it up (queued, running or held). */
export function aligning(jobs: readonly JobInfo[]): boolean {
  return jobs.some((j) => (isActive(j) || j.status === "held") && j.steps.some((s) => s.type === "align"));
}

/** Offer it: the lines' times are estimated from the text, the asset has its audio now, and no job is at it already. */
export function offerAlign(rec: Times, hasAudio: boolean, jobs: readonly JobInfo[]): boolean {
  return rec.times === "estimated" && hasAudio && !aligning(jobs);
}

/** What the lines' times are, for the meta line: estimated, or from the audio and how much of it was heard; null when
 * they came with the transcript or from transcription. */
export function timesLabel(rec: Times): string | null {
  if (rec.times === "estimated") return "Times estimated";
  if (rec.times !== "aligned") return null;
  const m = rec.aligned?.matched;
  return m != null ? `Times from the audio · ${Math.round(m * 100)}% of words heard` : "Times from the audio";
}
