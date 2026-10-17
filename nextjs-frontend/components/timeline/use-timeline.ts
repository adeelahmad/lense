"use client";

import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { Activities } from "@/app/openapi-client";
import type { ActivityKind, ActivityMark, ActivityPage, MarksOut } from "@/app/openapi-client/types.gen";
import { markOf, toQuery, type Activity, type Filters, type ListQuery, type Mark } from "@/components/timeline/model";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

export const PAGE = 50;
export const POLL_MS = 15_000;

/** The timeline for these filters, newest first, a page at a time ("Older" reads the next by `before`). */
export function useTimeline(filters: Filters) {
  const client = useApiClient();
  const query = useMemo(() => toQuery(filters), [filters]);
  return useInfiniteQuery({
    queryKey: ["timeline", query],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      data(
        Activities.listActivities({ client, query: { ...query, before: pageParam, limit: PAGE } }),
      ) as Promise<ActivityPage>,
    getNextPageParam: (last) => last.next ?? null,
    staleTime: 10_000,
    // new items come in through the pill rather than by the list moving under you
    refetchOnWindowFocus: false,
  });
}

/** What came in after `since` for the same filters: a light look every 15 seconds while the tab is visible. */
export function useNewItems(query: ListQuery, since: string | undefined, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["timeline-new", query, since],
    queryFn: () =>
      data(Activities.listActivities({ client, query: { ...query, since, limit: PAGE } })) as Promise<ActivityPage>,
    enabled: enabled && !!since,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });
}

/** The kinds of activity, for the filter chips and labels. */
export function useKinds() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["activity-kinds"],
    queryFn: () => data(Activities.listActivityKinds({ client })) as Promise<ActivityKind[]>,
    staleTime: 5 * 60_000,
  });
}

/** The person's marks: what the pages say, with their own changes on top as soon as they make them. `set` marks
 * activities (one or many), says so with an Undo, and puts them back if the server refuses. */
export function useMarks(server: Record<string, ActivityMark | undefined>) {
  const client = useApiClient();
  const toast = useToast();
  const [mine, setMine] = useState<Record<string, Mark>>({});
  const of = useCallback((id: string) => mine[id] ?? markOf(server, id), [mine, server]);

  const { mutate } = useMutation({
    mutationFn: async ({ ids, flags }: { ids: string[]; flags: Partial<Mark> }) => {
      const out: MarksOut = { marks: {}, unknown: [] };
      for (let i = 0; i < ids.length; i += 500) {
        const got = (await data(
          Activities.markActivities({ client, body: { ids: ids.slice(i, i + 500), ...flags } }),
        )) as MarksOut;
        Object.assign(out.marks, got.marks);
        out.unknown = [...(out.unknown ?? []), ...(got.unknown ?? [])];
      }
      return out;
    },
  });

  const set = useCallback(
    (ids: string[], flags: Partial<Mark>, say?: { title: string; undo?: boolean }) => {
      if (!ids.length) return;
      const before = Object.fromEntries(ids.map((id) => [id, of(id)]));
      setMine((m) => ({ ...m, ...Object.fromEntries(ids.map((id) => [id, { ...before[id], ...flags }])) }));
      mutate(
        { ids, flags },
        {
          onError: (e) => {
            setMine((m) => ({ ...m, ...before }));
            toast({ tone: "red", title: "Couldn’t save the mark", body: (e as Error).message });
          },
        },
      );
      if (say)
        toast({
          tone: "green",
          title: say.title,
          action: say.undo
            ? {
                label: "Undo",
                onClick: () => {
                  // put each back as it was, one call per mark they had before
                  const keys = Object.keys(flags) as (keyof Mark)[];
                  for (const k of keys)
                    for (const v of [true, false]) {
                      const back = ids.filter((id) => before[id][k] === v);
                      if (back.length) {
                        setMine((m) => ({
                          ...m,
                          ...Object.fromEntries(back.map((id) => [id, { ...(m[id] ?? before[id]), [k]: v }])),
                        }));
                        mutate({ ids: back, flags: { [k]: v } });
                      }
                    }
                },
              }
            : undefined,
        });
    },
    [of, mutate, toast],
  );
  return useMemo(() => ({ of, set }), [of, set]);
}

export type { Activity };
