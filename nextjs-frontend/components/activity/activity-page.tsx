"use client";

import { Server } from "lucide-react";
import Link from "next/link";

import { useJobEvents } from "@/components/activity/job-events";
import { RunsView } from "@/components/activity/runs-view";
import { useJobList, useWorkers } from "@/components/activity/use-activity";
import { WorkersView } from "@/components/activity/workers-view";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { Tooltip } from "@/components/ui/tooltip";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

function LiveBadge() {
  const feed = useJobEvents();
  const live = feed === "live";
  return (
    <Tooltip
      content={
        live ? "Rows update as runs change" : "Reconnecting to live updates; refreshing every few seconds meanwhile"
      }
    >
      <span tabIndex={0} className="flex items-center gap-1.5 text-[12.5px] font-medium text-fg-secondary">
        <span aria-hidden className={cn("size-[7px] rounded-full", live ? "bg-green" : "bg-fg-muted")} />
        {live ? "Live" : feed === "connecting" ? "Connecting…" : "Polling"}
      </span>
    </Tooltip>
  );
}

/** A1 / A3: every run, live, and (for admins) the workers that run them. */
export function ActivityPage({ tab }: { tab: "runs" | "workers" }) {
  const { admin } = useArchive();
  const list = useJobList({ limit: 200 });
  const workers = useWorkers();
  const total = Object.values(list.counts).reduce((a, n) => a + n, 0);
  const c = list.counts;
  const summary = [
    c.running ? `${c.running} running` : "",
    c.queued ? `${c.queued} queued` : "",
    c.failed ? `${c.failed} failed` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const tabs = (
    <Tabs
      aria-label="Activity"
      value={tab}
      items={[
        {
          value: "runs",
          label: "Runs",
          count: list.isSuccess ? total : undefined,
          href: "/activity",
        },
        {
          value: "workers",
          label: "Workers",
          count: admin && workers.data ? workers.data.length : undefined,
          href: "/activity?tab=workers",
        },
      ]}
    />
  );
  return (
    <div className="pb-10">
      <div className="flex flex-wrap items-center gap-3.5 px-4 pt-[18px] md:px-6">
        <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Activity</h1>
        <LiveBadge />
        <span className="flex-1" />
        <span className="text-[13px] font-medium text-fg-muted md:hidden">{summary}</span>
        {tab === "runs" && admin && (
          <Button asChild variant="secondary" size="sm" className="hidden md:inline-flex">
            <Link href="/activity?tab=workers">
              <Server /> Workers
            </Link>
          </Button>
        )}
      </div>
      {tab === "workers" ? (
        <>
          <div className="px-4 pt-2.5 md:px-6">{tabs}</div>
          <WorkersView />
        </>
      ) : (
        <RunsView tabs={tabs} />
      )}
    </div>
  );
}
