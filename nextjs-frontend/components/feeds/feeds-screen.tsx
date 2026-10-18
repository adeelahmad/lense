"use client";

import { useQuery } from "@tanstack/react-query";
import { Podcast as PodcastIcon, Rss } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Feeds } from "@/app/openapi-client";
import { CreateFeedDialog } from "@/components/feeds/create-feed-dialog";
import { itemsText, kindLabel } from "@/components/feeds/model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime, EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** /feeds: the published feeds in the namespaces you can read, each with what it holds and whether an app reads it. */
export function FeedsScreen() {
  const client = useApiClient();
  const { namespaces, namespace, can } = useArchive();
  const [creating, setCreating] = useState(false);
  const q = useQuery({
    queryKey: ["feeds", "list", namespace],
    queryFn: () => data(Feeds.listFeeds({ client, query: namespace ? { namespace } : undefined })),
  });
  const nsOf = (space: number, name?: string | null) => name ?? namespaces.find((n) => n.id === space)?.name;
  const editor = can("editor");
  const newFeed = editor && (
    <Button variant="primary" onClick={() => setCreating(true)}>
      New feed
    </Button>
  );
  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <PageHeader
        className="mb-0"
        title="Feeds"
        meta="Private RSS and podcast feeds that any feed reader or podcast app follows."
        actions={newFeed}
      />
      {q.isPending ? (
        <SkeletonRows rows={4} />
      ) : q.isError ? (
        <EmptyState tone="error" title="Couldn’t load feeds">
          {(q.error as Error).message}
        </EmptyState>
      ) : !q.data.length ? (
        <EmptyState icon={<Rss />} title="No feeds yet" actions={newFeed}>
          Make a feed and add its address to your reader. A routine fills it on a schedule with what’s new, or makes an
          episode about it; agents add to it through the API and MCP.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {q.data.map((f) => (
            <li key={f.id}>
              <Link
                href={`/feeds/${f.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-surface"
              >
                {f.kind === "podcast" ? (
                  <PodcastIcon aria-hidden className="size-4 shrink-0 text-fg-secondary" />
                ) : (
                  <Rss aria-hidden className="size-4 shrink-0 text-fg-secondary" />
                )}
                <span className="min-w-0 flex-1 truncate font-semibold text-fg">{f.title}</span>
                <Badge tone={f.kind === "podcast" ? "intent" : "neutral"}>{kindLabel(f.kind)}</Badge>
                <span className="text-[13px] text-fg-secondary">{nsOf(f.space, f.namespace)}</span>
                <span className="tabular w-24 text-right text-[13px] text-fg-secondary">{itemsText(f.items)}</span>
                <span className="w-36 text-right text-[13px] text-fg-muted">
                  {f.fetched_at ? (
                    <>
                      read <DateTime iso={f.fetched_at} />
                    </>
                  ) : (
                    "not read by an app yet"
                  )}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <CreateFeedDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}
