"use client";

/** What the studio reads from the API: automations, the actions catalog and the kinds of activity. */
import { useQuery } from "@tanstack/react-query";

import { Activities, Automations } from "@/app/openapi-client";
import type { ActivityKind } from "@/app/openapi-client/types.gen";
import type { ActionDef } from "@/components/workflows/workflow-model";
import { data, useApiClient } from "@/lib/api/browser";

export function useAutomations() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["automations"],
    queryFn: () => data(Automations.listAutomations({ client })),
  });
}

export function useActions() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["automation-actions"],
    queryFn: async () => (await data(Automations.listActions({ client }))) as ActionDef[],
    staleTime: 300_000,
  });
}

export function useKinds() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["activity-kinds"],
    queryFn: async () => (await data(Activities.listActivityKinds({ client }))) as ActivityKind[],
    staleTime: 300_000,
  });
}
