"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { Recordings, Speakers } from "@/app/openapi-client";
import type { Player, Speaker } from "@/app/openapi-client/types.gen";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/**
 * Every recording you can read (up to the API's 1,000), for titles, dates, durations and whether they have audio.
 * Shared by search, chat scope pickers and batch lists.
 */
export function useRecordingIndex(enabled = true) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["recording-index"],
    queryFn: () => data(Recordings.listRecordings({ client, query: { limit: 1000 } })),
    staleTime: 60_000,
    enabled,
  });
  const byId = useMemo(() => new Map((q.data ?? []).map((r) => [r.id, r])), [q.data]);
  return { ...q, byId };
}

export type DirectorySpeaker = Speaker & { namespace: string };

/** Speakers of every namespace you can read (one request per namespace), for pickers and name lookups. */
export function useSpeakerDirectory(enabled = true) {
  const client = useApiClient();
  const { namespaces } = useArchive();
  const results = useQueries({
    queries: namespaces.map((n) => ({
      queryKey: ["speakers", n.name],
      queryFn: () => data(Speakers.listSpeakers({ client, query: { ns: n.name } })),
      staleTime: 60_000,
      enabled,
    })),
  });
  const speakers = useMemo<DirectorySpeaker[]>(
    () =>
      results.flatMap((r, i) =>
        (r.data?.speakers ?? []).map((s) => ({
          ...s,
          namespace: namespaces[i]?.name ?? "",
        })),
      ),
    [results.map((r) => r.dataUpdatedAt).join(","), namespaces],
  );
  return {
    speakers,
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
  };
}

export type PlayerSegment = {
  t0: number;
  t1: number;
  s?: string | null;
  text: string;
  e?: string | null;
};

/** The segments of player data, typed. */
export function playerSegments(p: Player | undefined): PlayerSegment[] {
  return ((p?.segments ?? []) as unknown[]).filter(
    (x): x is PlayerSegment => !!x && typeof (x as PlayerSegment).t0 === "number",
  );
}

/** Player speakers: key ("s2") → speaker id and name. */
export function playerSpeakers(p: Player | undefined): { key: string; id: number | null; name: string }[] {
  return ((p?.speakers ?? []) as { key?: string; id?: number; name?: string }[]).map((s) => ({
    key: String(s.key ?? ""),
    id: s.id ?? null,
    name: s.name ?? "Speaker",
  }));
}
