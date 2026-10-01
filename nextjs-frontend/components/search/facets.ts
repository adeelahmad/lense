import type { SearchFacets, SearchHit } from "@/app/openapi-client/types.gen";
import { objectName } from "@/components/recording/objects-model";

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
  /** Kinds of object seen in the recordings, counted in recordings rather than moments. */
  objects: FacetValue[];
};

/**
 * The server's counts over every matching moment (`facets=true`) as the panel shows them. A speaker's namespace shows
 * only when two people with that name are listed.
 */
export function fromServer(f: SearchFacets): Facets {
  const names = new Map<string, number>();
  for (const s of f.speakers ?? []) names.set(s.name, (names.get(s.name) ?? 0) + 1);
  return {
    namespaces: (f.namespaces ?? []).map((n) => ({ key: n.name, label: n.name, count: n.count })),
    speakers: (f.speakers ?? []).map((s) => ({
      key: String(s.id),
      id: s.id,
      label: s.name,
      count: s.count,
      sub: (names.get(s.name) ?? 0) > 1 ? (s.namespace ?? undefined) : undefined,
    })),
    emotions: (f.emotions ?? []).map((e) => ({ key: e.name, label: e.name, count: e.count })),
    recordings: (f.recordings ?? []).map((r) => ({
      key: String(r.id),
      id: r.id,
      label: r.title || `Recording ${r.id}`,
      count: r.count,
    })),
    objects: (f.objects ?? []).map((o) => ({ key: o.name, label: objectName(o.name), count: o.count })),
  };
}

export type HitGroup = {
  recordingId: number;
  title: string;
  namespace: string | null;
  recordedAt: string | null;
  hits: SearchHit[];
};

/** Hits grouped by recording, in the order their best hit ranks; hits inside a group in time order (untimed last). */
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
  // lines of files without times come after the moments, in their order
  const at = (h: SearchHit) => h.t0 ?? Number.MAX_SAFE_INTEGER;
  for (const g of groups.values()) g.hits.sort((a, b) => at(a) - at(b) || (a.line ?? 0) - (b.line ?? 0));
  return [...groups.values()];
}
