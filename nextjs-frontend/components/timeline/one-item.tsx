"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState, type ComponentProps } from "react";

import { Activities } from "@/app/openapi-client";
import type { StreamActivity } from "@/app/openapi-client/types.gen";
import { TimelineRow } from "@/components/timeline/item";
import { group, type Activity } from "@/components/timeline/model";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

/** One activity by its id (GET /api/v1/activities/{id}), for `/timeline?item=<id>`: the link a comment, a reaction
 * or an approval gives to the item it is about. */
export function useOneItem(id: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["timeline-item", id],
    queryFn: () => data(Activities.getActivity({ client, path: { activity_id: id } })) as Promise<StreamActivity>,
    enabled: !!id,
    staleTime: 10_000,
  });
}

type RowProps = ComponentProps<typeof TimelineRow>;

/** The item the address names, opened above the timeline, with the same marks, reactions, comments and actions as
 * any line, and a way back to the whole timeline. */
export function OneItem({
  id,
  a,
  error,
  back,
  ...row
}: {
  id: string;
  a: Activity | undefined;
  error: Error | null;
  /** Where "Back to the timeline" goes. */
  back: string;
} & Pick<
  RowProps,
  | "namespaces"
  | "labels"
  | "markOf"
  | "onMark"
  | "onHide"
  | "onMute"
  | "reactionsOf"
  | "commentsOf"
  | "caused"
  | "social"
>) {
  const [open, setOpen] = useState(true);
  const g = a ? group([a])[0] : null;
  return (
    <section aria-label="This item" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-secondary">
        <span className="font-semibold">This item</span>
        <Link href={back} className="inline-flex items-center gap-1 text-fg-accent hover:underline">
          <ArrowLeft className="size-3.5" aria-hidden />
          Back to the timeline
        </Link>
      </div>
      {error ? (
        <EmptyState title="No such item">{error.message}</EmptyState>
      ) : g ? (
        <ul className="flex flex-col rounded-md border border-border bg-surface px-3">
          <TimelineRow group={g} open={open} onToggle={() => setOpen((o) => !o)} focused={false} {...row} />
        </ul>
      ) : (
        <Skeleton className="h-12" aria-label={`Loading ${id}`} />
      )}
    </section>
  );
}
