"use client";

import { useQuery } from "@tanstack/react-query";
import { Podcast as PodcastIcon } from "lucide-react";
import Link from "next/link";

import { Podcasts } from "@/app/openapi-client";
import { inProgress, statusLabel, statusTone } from "@/components/podcasts/model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/** /podcasts: the episodes in the namespaces you can read, newest first. */
export function PodcastsScreen() {
  const client = useApiClient();
  const { namespaces } = useArchive();
  const q = useQuery({
    queryKey: ["podcasts", "list"],
    queryFn: () => data(Podcasts.listPodcasts({ client, query: { limit: 200 } })),
    refetchInterval: (query) => (query.state.data?.some((p) => inProgress(p.status)) ? 5000 : false),
  });
  const nsOf = (space: number) => namespaces.find((n) => n.id === space)?.name;
  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <PageHeader className="mb-0" title="Podcasts" meta="Two hosts talk through what you pick, citing every claim." />
      {q.isPending ? (
        <SkeletonRows rows={5} />
      ) : q.isError ? (
        <EmptyState tone="error" title="Couldn’t load podcasts">
          {(q.error as Error).message}
        </EmptyState>
      ) : !q.data.length ? (
        <EmptyState
          icon={<PodcastIcon />}
          title="No podcasts yet"
          actions={
            <Button asChild variant="primary">
              <Link href="/library">Pick something in the Library</Link>
            </Button>
          }
        >
          Select recordings or documents in the Library and choose Podcast, or open one and pick Create podcast from its
          menu.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {q.data.map((p) => (
            <li key={p.id}>
              <Link
                href={`/podcasts/${p.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-surface"
              >
                <PodcastIcon aria-hidden className="size-4 shrink-0 text-fg-secondary" />
                <span className="min-w-0 flex-1 truncate font-semibold text-fg">
                  {p.title || (inProgress(p.status) ? "New podcast" : "Untitled podcast")}
                </span>
                <Badge tone={statusTone(p.status)} dot>
                  {statusLabel(p.status)}
                </Badge>
                <span className="text-[13px] text-fg-secondary">{nsOf(p.space)}</span>
                <span className="tabular w-14 text-right text-[13px] text-fg-secondary">
                  {p.duration_ms ? tc(p.duration_ms) : ""}
                </span>
                <DateTime iso={p.created_at} className="w-24 text-right text-[13px] text-fg-muted" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
