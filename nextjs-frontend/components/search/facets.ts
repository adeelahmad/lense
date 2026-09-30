import type { SearchHit } from "@/app/openapi-client/types.gen";

/** One value of a facet with how many moments have it. */
export type FacetValue = {
  key: string;
  label: string;
  count: number;
  sub?: string;
  id?: number;
};

export type Facets = {
  namespaces: FacetValue[];
  speakers: FacetValue[];
  emotions: FacetValue[];
  recordings: FacetValue[];
};

function tally(values: { key: string; label: string; sub?: string; id?: number }[]): FacetValue[] {
  const m = new Map<string, FacetValue>();
  for (const v of values) {
    const cur = m.get(v.key);
    if (cur) cur.count += 1;
    else m.set(v.key, { ...v, count: 1 });
  }
  return [...m.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/**
 * Facet counts from a set of hits. Speakers are namespace-scoped, so two people with the same name in different
 * namespaces stay apart and show their namespace.
 */
export function computeFacets(hits: SearchHit[]): Facets {
  const speakers = tally(
    hits
      .filter((h) => h.speaker_id != null)
      .map((h) => ({
        key: String(h.speaker_id),
        id: h.speaker_id as number,
        label: h.speaker || `Speaker ${h.speaker_id}`,
        sub: h.namespace ?? undefined,
      })),
  );
  const names = new Map<string, number>();
  for (const s of speakers) names.set(s.label, (names.get(s.label) ?? 0) + 1);
  return {
    namespaces: tally(
      hits
        .filter((h) => h.namespace)
        .map((h) => ({
          key: h.namespace as string,
          label: h.namespace as string,
        })),
    ),
    speakers: speakers.map((s) => ((names.get(s.label) ?? 0) > 1 ? s : { ...s, sub: undefined })),
    emotions: tally(
      hits
        .filter((h) => h.emotion && h.emotion !== "Unknown")
        .map((h) => ({ key: h.emotion as string, label: h.emotion as string })),
    ),
    recordings: tally(
      hits.map((h) => ({
        key: String(h.recording_id),
        id: h.recording_id,
        label: h.title || `Recording ${h.recording_id}`,
      })),
    ),
  };
}

export type HitGroup = {
  recordingId: number;
  title: string;
  namespace: string | null;
  recordedAt: string | null;
  hits: SearchHit[];
};

/** Hits grouped by recording, in the order their best hit ranks; hits inside a group in time order. */
export function groupByRecording(hits: SearchHit[]): HitGroup[] {
  const groups = new Map<number, HitGroup>();
  for (const h of hits) {
    let g = groups.get(h.recording_id);
    if (!g) {
      g = {
        recordingId: h.recording_id,
        title: h.title || `Recording ${h.recording_id}`,
        namespace: h.namespace ?? null,
        recordedAt: h.recorded_at ?? null,
        hits: [],
      };
      groups.set(h.recording_id, g);
    }
    g.hits.push(h);
  }
  for (const g of groups.values()) g.hits.sort((a, b) => a.t0 - b.t0);
  return [...groups.values()];
}
