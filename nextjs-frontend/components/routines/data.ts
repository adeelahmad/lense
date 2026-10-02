"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { Routines, Sources } from "@/app/openapi-client";
import type { GraphChange, Watch } from "@/app/openapi-client/types.gen";
import { usePipelineCatalog, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import type { Names } from "@/components/routines/routine-model";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export function useRoutines() {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["routines"],
    queryFn: () => data(Routines.listRoutines({ client })),
    enabled: admin,
    // A run asked for starts within half a minute; keep "Running" and the last status fresh.
    refetchInterval: 15_000,
  });
}

export function useGraphChanges(status: GraphChange["status"], run?: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["graph-changes", status, run ?? null],
    queryFn: () => data(Routines.listGraphChanges({ client, query: { status, run: run ?? null } })),
    staleTime: 10_000,
  });
}

export function useWatches(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["watches"],
    queryFn: () => data(Sources.listWatches({ client })),
    enabled,
    staleTime: 30_000,
  });
}

export function watchName(w: Watch): string {
  return `${w.source_name ?? "source"}: ${w.path || "/"}`;
}

/** Names of the watched folders, pipelines and workflows routines point at. */
export function useNames(): Names {
  const { admin } = useArchive();
  const watches = useWatches(admin);
  const pipelines = usePipelineCatalog();
  const workflows = useWorkflowCatalog();
  return useMemo(() => {
    const w = new Map(((watches.data ?? []) as Watch[]).map((x) => [x.id, watchName(x)]));
    const p = new Map((pipelines.data?.pipelines ?? []).map((x) => [x.id, x.name]));
    const f = new Map((workflows.data?.workflows ?? []).map((x) => [x.id, { name: x.name, scope: x.scope }]));
    return { watch: (id) => w.get(id), pipeline: (id) => p.get(id), workflow: (id) => f.get(id) };
  }, [watches.data, pipelines.data, workflows.data]);
}
