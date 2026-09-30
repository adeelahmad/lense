"use client";

import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";

import { Pipelines } from "@/app/openapi-client";
import type { Pipeline } from "@/app/openapi-client/types.gen";
import { stepLabel } from "@/components/activity/job-model";
import { usePipelineCatalog } from "@/components/pipelines/catalog-header";
import { toSpec } from "@/components/pipelines/pipeline-model";
import { data, useApiClient } from "@/lib/api/browser";

/** Which pipeline steps use each template: template id → ["Podcast standard v4 → LLM", ...]. */
export function useTemplateUsage(): {
  usage: Map<number, string[]>;
  loading: boolean;
} {
  const client = useApiClient();
  const catalog = usePipelineCatalog();
  const list = catalog.data?.pipelines ?? [];
  const details = useQueries({
    queries: list.map((p) => ({
      queryKey: ["pipeline", p.id],
      queryFn: () => data(Pipelines.getPipeline({ client, path: { pid: p.id } })),
      staleTime: 30_000,
    })),
  });
  const ready = details.map((d) => d.data as Pipeline | undefined);
  const key = ready.map((d) => `${d?.id}:${d?.version}`).join(",");
  const usage = useMemo(() => {
    const m = new Map<number, string[]>();
    for (const p of ready) {
      if (!p) continue;
      for (const raw of p.steps) {
        const s = toSpec(raw);
        if (s.template == null) continue;
        const arr = m.get(s.template) ?? [];
        arr.push(`${p.name} v${p.version} → ${stepLabel(s.type, s.name)}${s.version ? ` (pins v${s.version})` : ""}`);
        m.set(s.template, arr);
      }
    }
    return m;
  }, [key]);
  return {
    usage,
    loading: catalog.isLoading || details.some((d) => d.isLoading),
  };
}
