"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";

import { Pipelines, Templates, Workflows } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export function usePipelineCatalog() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 30_000,
  });
}

export function useTemplateList() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["templates"],
    queryFn: () => data(Templates.listTemplates({ client })),
    staleTime: 30_000,
  });
}

export function useWorkflowCatalog() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workflows"],
    queryFn: () => data(Workflows.listWorkflows({ client })),
    staleTime: 30_000,
  });
}

const NEW = {
  pipelines: ["/pipelines/new", "New pipeline"],
  workflows: ["/workflows/new", "New workflow"],
  templates: ["/templates/new", "New template"],
} as const;

/** PL1 header: "Pipelines" with the Pipelines / Workflows / Templates tabs and the one "New" action. */
export function CatalogHeader({ tab }: { tab: "pipelines" | "workflows" | "templates" }) {
  const { admin } = useArchive();
  const pipelines = usePipelineCatalog();
  const workflows = useWorkflowCatalog();
  const templates = useTemplateList();
  const [newHref, newLabel] = NEW[tab];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <h1 className="flex-1 text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Pipelines</h1>
        {admin ? (
          <Button asChild size="sm" variant="primary">
            <Link href={newHref}>
              <Plus /> {newLabel}
            </Link>
          </Button>
        ) : (
          <Button size="sm" variant="primary" icon={<Plus />} disabled disabledReason={`Only admins can create ${tab}`}>
            {newLabel}
          </Button>
        )}
      </div>
      <Tabs
        aria-label="Pipelines, workflows and templates"
        value={tab}
        items={[
          {
            value: "pipelines",
            label: "Pipelines",
            count: pipelines.data ? pipelines.data.pipelines.length + 1 : undefined,
            href: "/pipelines",
          },
          {
            value: "workflows",
            label: "Workflows",
            count: workflows.data?.workflows.length,
            href: "/workflows",
          },
          {
            value: "templates",
            label: "Templates",
            count: templates.data?.length,
            href: "/templates",
          },
        ]}
      />
    </div>
  );
}
