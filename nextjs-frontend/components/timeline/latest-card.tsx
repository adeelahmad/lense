"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Activities } from "@/app/openapi-client";
import type { ActivityPage } from "@/app/openapi-client/types.gen";
import { TimelineRow } from "@/components/timeline/item";
import { UNMARKED } from "@/components/timeline/model";
import { POLL_MS } from "@/components/timeline/use-timeline";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

/** Home's Latest card: the five newest activities you haven't reviewed, and the way to the timeline. */
export function LatestCard() {
  const client = useApiClient();
  const latest = useQuery({
    queryKey: ["timeline-latest"],
    queryFn: () =>
      data(
        Activities.listActivities({ client, query: { reviewed: false, hidden: false, limit: 5 } }),
      ) as Promise<ActivityPage>,
    refetchInterval: POLL_MS * 4,
    staleTime: 15_000,
  });
  const items = latest.data?.items ?? [];
  return (
    <section aria-labelledby="latest" className="flex flex-col gap-2 rounded-lg border border-border p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="latest" className="text-[13px] font-bold leading-none text-fg-secondary">
          Latest
        </h2>
        <Link href="/timeline?unreviewed=1" className="text-[12.5px] font-semibold text-fg-accent hover:underline">
          Timeline
        </Link>
      </div>
      {latest.isLoading ? (
        <Skeleton className="w-2/3" />
      ) : latest.error ? (
        <p className="text-[13px] text-fg-muted">Couldn’t load the latest: {(latest.error as Error).message}</p>
      ) : items.length ? (
        <ul className="flex flex-col">
          {items.map((a) => (
            <TimelineRow
              key={a.id}
              compact
              group={{ key: a.id, items: [a] }}
              namespaces={latest.data?.namespaces ?? {}}
              labels={{}}
              markOf={() => UNMARKED}
              onMark={() => undefined}
              open={false}
              onToggle={() => undefined}
            />
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-fg-muted">You’ve reviewed everything that came in.</p>
      )}
    </section>
  );
}
