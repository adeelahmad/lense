"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";

import { Pipelines, Templates } from "@/app/openapi-client";
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

/** PL1 header: "Pipelines" with the Pipelines / Templates tabs and the one "New" action. */
export function CatalogHeader({ tab }: { tab: "pipelines" | "templates" }) {
  const { admin } = useArchive();
  const pipelines = usePipelineCatalog();
  const templates = useTemplateList();
  const newHref = tab === "pipelines" ? "/pipelines/new" : "/templates/new";
  const newLabel = tab === "pipelines" ? "New pipeline" : "New template";
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
        aria-label="Pipelines and templates"
        value={tab}
        items={[
          {
            value: "pipelines",
            label: "Pipelines",
            count: pipelines.data ? pipelines.data.pipelines.length + 1 : undefined,
            href: "/pipelines",
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
