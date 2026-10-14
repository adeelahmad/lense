"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { Search } from "@/app/openapi-client";
import type { SimilarCite, SimilarResults } from "@/app/openapi-client/types.gen";
import { recordingHref } from "@/components/search/links";
import { data, useApiClient } from "@/lib/api/browser";

/** What to find content like: selected text (and the resource it was selected in), a resource, a topic or an entity. */
export type SimilarBasis =
  { text: string; exclude?: number | null } | { recording: number } | { topic: number } | { entity: number };

/** The most of a selection that's compared (the API's limit). */
export const TEXT_MAX = 2000;
/** The fewest characters a selection needs before it's worth comparing. */
export const TEXT_MIN = 3;

/** The basis as the API's query parameters. */
export function basisQuery(b: SimilarBasis) {
  if ("text" in b) return { text: b.text.slice(0, TEXT_MAX), exclude: b.exclude ?? undefined };
  if ("recording" in b) return { recording: b.recording };
  if ("topic" in b) return { topic: b.topic };
  return { entity: b.entity };
}

function basisKey(b: SimilarBasis): string {
  return JSON.stringify(basisQuery(b));
}

/** The first `limit` resources like `basis`. */
export function useSimilar(basis: SimilarBasis | null, { limit = 10, enabled = true } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["similar", basis ? basisKey(basis) : null, limit],
    queryFn: () => data(Search.findSimilar({ client, query: { ...basisQuery(basis!), limit } })),
    enabled: enabled && basis != null,
    staleTime: 60_000,
  });
}

/** Every resource like `basis`, a page at a time (the full Similar page). */
export function useSimilarPages(basis: SimilarBasis | null, pageSize = 20) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: ["similar-pages", basis ? basisKey(basis) : null, pageSize],
    queryFn: ({ pageParam }) =>
      data(Search.findSimilar({ client, query: { ...basisQuery(basis!), limit: pageSize, offset: pageParam } })),
    initialPageParam: 0,
    getNextPageParam: (last: SimilarResults, pages: SimilarResults[]) => {
      const seen = pages.reduce((n, p) => n + p.items.length, 0);
      return seen < last.total && last.items.length ? seen : undefined;
    },
    enabled: basis != null,
    staleTime: 60_000,
  });
}

/** The full Similar page for a basis ("Find more"). */
export function similarHref(b: SimilarBasis): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(basisQuery(b))) if (v != null && v !== "") q.set(k, String(v));
  return `/similar?${q}`;
}

/** The basis a Similar page's address asks for, or null. */
export function basisFromParams(p: URLSearchParams): SimilarBasis | null {
  const n = (k: string) => {
    const v = p.get(k);
    return v && /^\d+$/.test(v) ? Number(v) : null;
  };
  const text = p.get("text")?.trim();
  if (text) return { text, exclude: n("exclude") };
  const recording = n("recording");
  if (recording != null) return { recording };
  const topic = n("topic");
  if (topic != null) return { topic };
  const entity = n("entity");
  if (entity != null) return { entity };
  return null;
}

/** Where a cited moment opens: its page of a document, or its time. */
export function citeHref(recordingId: number, c: Pick<SimilarCite, "page" | "t0">): string {
  return c.page != null ? `/resources/${recordingId}?page=${c.page}` : recordingHref(recordingId, c.t0);
}

/** Why a result is alike, in words. */
export const MATCH_LABEL: Record<string, string> = {
  meaning: "Close in meaning",
  words: "Shares key words",
  topics: "Same topics",
  entities: "Same entities",
};
