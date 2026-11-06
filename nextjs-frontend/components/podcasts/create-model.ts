/** Output modes supported by the podcast pipeline. Style is kept separate for existing callers. */
export const MODES = [
  {
    value: "dialogue",
    label: "Dialogue · two voices",
    hint: "Two hosts discuss the sources, with citations and fact-checking.",
  },
  {
    value: "original",
    label: "Original · one voice",
    hint: "Read the selected source text without rewriting. Duration comes from the source, not a target length.",
  },
  {
    value: "summary",
    label: "Summary · one voice",
    hint: "A concise narrated summary of the selected sources, with citations and fact-checking.",
  },
  {
    value: "explanation",
    label: "Explanation · one voice",
    hint: "A single narrator explains the selected sources step by step, with citations and fact-checking.",
  },
] as const;

export type PodcastMode = (typeof MODES)[number]["value"];

/** Blank choices retain the podcast defaults; single-narrator modes never submit a second voice. */
export function selectedVoices(mode: PodcastMode, a: string, b: string): { a: string | null; b: string | null } | null {
  const second = mode === "dialogue" ? b || null : null;
  return a || second ? { a: a || null, b: second } : null;
}
