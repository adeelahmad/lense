import type { RecordingSummary, Speaker } from "@/app/openapi-client/types.gen";

export type ReviewPair = { a: Speaker; b: Speaker; score: number };

/** Pairs waiting for a person: each new voice that was close to a known one (between review and auto-match). */
export function reviewPairs(speakers: Speaker[]): ReviewPair[] {
  const byId = new Map(speakers.map((s) => [s.id, s]));
  const out: ReviewPair[] = [];
  for (const s of speakers)
    for (const g of s.suggestions ?? []) {
      const b = byId.get(g.id);
      if (b) out.push({ a: s, b, score: g.score });
    }
  return out.sort((x, y) => y.score - x.score);
}

/** When each speaker was last heard, from the recordings list (it names who speaks in each). */
export function lastHeard(speakers: Speaker[], recordings: RecordingSummary[], ns: string): Map<number, string> {
  const latest = new Map<string, string>();
  for (const r of recordings) {
    if (r.namespace !== ns || !r.recorded_at) continue;
    for (const name of String((r as { speakers?: unknown }).speakers ?? "").split(",")) {
      const n = name.trim();
      if (n && (!latest.has(n) || latest.get(n)! < r.recorded_at)) latest.set(n, r.recorded_at);
    }
  }
  const out = new Map<number, string>();
  for (const s of speakers) {
    const at = latest.get(s.display);
    if (at) out.set(s.id, at);
  }
  return out;
}
