"use client";

import { useQuery } from "@tanstack/react-query";

import { Projects } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";

/** Your projects, the most recently changed first: the archived ones instead, with `archived`. */
export function useProjects(archived = false) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["projects", { archived }],
    queryFn: () => data(Projects.listProjects({ client, query: { archived } })),
  });
}

/** A project with its threads, fetched again every few seconds while one of them is being answered. */
export function useProject(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["project", id],
    queryFn: () => data(Projects.getProject({ client, path: { pid: id } })),
    refetchInterval: (q) => (q.state.data?.thread_list?.some((t) => t.answering) ? 3000 : false),
  });
}
