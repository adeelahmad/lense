import type { Tone } from "@/components/ui/badge";

/** The stages an episode goes through, in order, with the status the backend gives it during each. */
export const STAGES = [
  { status: "gathering", label: "Gathering" },
  { status: "planning", label: "Planning" },
  { status: "writing", label: "Writing" },
  { status: "checking", label: "Fact-checking" },
  { status: "rendering", label: "Recording the voices" },
  { status: "publishing", label: "Publishing" },
] as const;

export const LENGTHS = [5, 10, 20] as const;

export const STYLES = [
  { value: "deep-dive", label: "Deep dive", hint: "Builds understanding step by step, with examples." },
  { value: "recap", label: "Quick recap", hint: "The essentials only, brisk." },
  { value: "debate", label: "Compare and contrast", hint: "Where the sources agree and where they differ." },
  { value: "beginner", label: "For a beginner", hint: "No jargon without a plain explanation." },
] as const;
export type Style = (typeof STYLES)[number]["value"];

const DONE = new Set(["ready", "script_only", "failed"]);

/** Whether the episode is still being made (its page polls until it isn't). */
export function inProgress(status: string | null | undefined): boolean {
  return !DONE.has(status ?? "");
}

/** The step it's on, 0-based; -1 while queued, STAGES.length once done. */
export function stageIndex(status: string | null | undefined): number {
  if (DONE.has(status ?? "")) return STAGES.length;
  return STAGES.findIndex((s) => s.status === status);
}

export function statusLabel(status: string | null | undefined): string {
  switch (status) {
    case "ready":
      return "Ready";
    case "script_only":
      return "Script only";
    case "failed":
      return "Failed";
    case "queued":
    case null:
    case undefined:
      return "Queued";
    default:
      return STAGES.find((s) => s.status === status)?.label ?? status;
  }
}

export function statusTone(status: string | null | undefined): Tone {
  if (status === "ready") return "green";
  if (status === "script_only") return "gate";
  if (status === "failed") return "red";
  return "intent";
}

/** A citation's link: the source at its moment (?t= in seconds) or on its page. */
export function sourceHref(ref: {
  recording?: number | null;
  t0?: number | null;
  page?: number | null;
}): string | null {
  if (ref.recording == null) return null;
  const base = `/resources/${ref.recording}`;
  if (ref.page != null && ref.page > 0) return `${base}?page=${ref.page}`;
  if (ref.t0 != null && ref.t0 > 0) return `${base}?t=${Math.floor(ref.t0 / 1000)}`;
  return base;
}

/** The hosts' names from what was asked, with the defaults the backend uses. */
export function hostNames(request: Record<string, unknown> | null | undefined): { a: string; b: string } {
  const h = (request?.hosts ?? {}) as { a?: string; b?: string };
  return { a: h.a || "Alex", b: h.b || "Sam" };
}
