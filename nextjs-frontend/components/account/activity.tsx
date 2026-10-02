"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import Link from "next/link";

import { Analytics } from "@/app/openapi-client";
import type { ActivityEntry } from "@/app/openapi-client/types.gen";
import { activityLine } from "@/components/analytics/model";
import { Button } from "@/components/ui/button";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { shortDate } from "@/lib/format";

const PAGE = 50;

/** Your own activity (docs/analytics.md): what Lens keeps about what you did, the latest first. */
export function ActivityPage() {
  const client = useApiClient();
  const mine = useInfiniteQuery({
    queryKey: ["analytics", "me"],
    queryFn: ({ pageParam }) =>
      data(Analytics.myActivity({ client, query: { limit: PAGE, before: pageParam ?? undefined } })),
    initialPageParam: null as string | null,
    getNextPageParam: (last: ActivityEntry[]) => (last.length === PAGE ? last[last.length - 1].at : undefined),
    staleTime: 0,
  });
  const rows = mine.data?.pages.flat() ?? [];
  return (
    <div className="px-4 py-5 sm:px-6">
      <section className="mx-auto flex max-w-[1000px] flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6">
        <div className="flex flex-col gap-1">
          <h1 className="text-[22px] font-bold leading-[1.2] text-fg">Your activity</h1>
          <p className="text-[13.5px] leading-[1.4] text-fg-secondary">
            What Lens keeps about what you did: the resources you opened, played, downloaded from and commented on, and
            that you searched, each with its time. Nothing else is kept: not your address, not your browser, not what
            you searched for. An admin sets how long it stays (90 days unless changed); the owners of a namespace see
            how its resources are used.
          </p>
        </div>
        {mine.isPending ? (
          <SkeletonRows rows={4} />
        ) : mine.isError ? (
          <EmptyState
            tone="error"
            icon={<History />}
            title="Couldn’t load your activity"
            actions={<Button onClick={() => mine.refetch()}>Try again</Button>}
          >
            {mine.error.message}
          </EmptyState>
        ) : !rows.length ? (
          <EmptyState icon={<History />} title="Nothing yet">
            What you open, play, search, download and comment on will be listed here.
          </EmptyState>
        ) : (
          <>
            <ol
              aria-label="Your activity"
              className="flex flex-col divide-y divide-border rounded-md border border-border"
            >
              {rows.map((e, i) => {
                const line = activityLine(e);
                return (
                  <li key={`${e.at}-${i}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3.5 py-2.5">
                    <span className="min-w-0 flex-1 text-[13.5px] leading-[1.4] text-fg">
                      {line.did}{" "}
                      {e.title && e.resource != null ? (
                        <Link href={`/resources/${e.resource}`} className="font-semibold text-fg hover:underline">
                          {line.what}
                        </Link>
                      ) : (
                        line.what && <span className="font-semibold text-fg-secondary">{line.what}</span>
                      )}
                    </span>
                    <time dateTime={e.at} className="tabular whitespace-nowrap text-[12.5px] text-fg-muted">
                      {shortDate(e.at, true)}
                    </time>
                  </li>
                );
              })}
            </ol>
            {mine.hasNextPage && (
              <div className="flex justify-center">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => mine.fetchNextPage()}
                  disabled={mine.isFetchingNextPage}
                >
                  {mine.isFetchingNextPage ? "Loading…" : "Show earlier"}
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
