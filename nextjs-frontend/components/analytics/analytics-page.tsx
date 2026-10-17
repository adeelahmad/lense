"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Admin } from "@/app/openapi-client";
import { UsageNumbers } from "@/components/analytics/usage-numbers";
import { SegmentedChoice } from "@/components/settings/controls";
import { Banner } from "@/components/ui/banner";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const PERIODS = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "180 days" },
];

const EVENT_LABEL: Record<string, string> = {
  lens_install: "Install",
  lens_setup_done: "Setup done",
  lens_daily_active: "Daily active",
  lens_feature_used: "Feature used",
  lens_upgrade: "Upgrade",
  lens_inactive: "Inactive",
  lens_return: "Return",
};

function params(p: Record<string, unknown> | undefined) {
  return Object.entries(p ?? {})
    .map(([k, v]) => `${k.replaceAll("_", " ")} ${String(v)}`)
    .join(" · ");
}

/** The events Lens counted each day, and whether each went to Google Analytics. */
function EventLog() {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["usage-events"],
    queryFn: () => data(Admin.usageEvents({ client, query: { limit: 200 } })),
  });
  if (q.isError) return <Banner tone="error">{q.error.message}</Banner>;
  if (!q.data) return <Skeleton className="h-24" />;
  if (!q.data.length)
    return (
      <p className="text-[12.5px] text-fg-muted">
        No events yet. Lens counts the day before once a day, so the first ones show up tomorrow.
      </p>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-left text-fg-muted">
            <th className="py-1 pr-3 font-normal">Day</th>
            <th className="py-1 pr-3 font-normal">Event</th>
            <th className="py-1 pr-3 font-normal">Details</th>
            <th className="py-1 pr-3 font-normal">Google Analytics</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((e, i) => (
            <tr key={`${e.at}-${e.name}-${i}`} className="border-t border-border-subtle">
              <td className="py-1 pr-3 tabular-nums text-fg">{e.day}</td>
              <td className="py-1 pr-3 text-fg">{EVENT_LABEL[e.name] ?? e.name}</td>
              <td className="py-1 pr-3 text-fg-secondary">{params(e.params as Record<string, unknown>)}</td>
              <td className="py-1 pr-3 text-fg-muted">
                {e.sent === true ? "sent" : e.sent === false ? "waiting" : "kept here only"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Built-in analytics (docs/analytics.md): works with Google Analytics off; GA is an optional export. */
export function AnalyticsPage() {
  const { admin } = useArchive();
  const client = useApiClient();
  const [days, setDays] = useState("30");
  const ga = useQuery({
    queryKey: ["analytics-status"],
    queryFn: () => data(Admin.analyticsStatus({ client })),
    enabled: admin,
  });
  if (!admin)
    return (
      <div className="px-4 py-[18px] sm:px-7">
        <EmptyState title="Analytics are for admins">Ask an admin of this archive.</EmptyState>
      </div>
    );
  const s = ga.data;
  return (
    <div className="flex flex-col gap-4 px-4 py-[18px] sm:px-7">
      <PageHeader
        title="Analytics"
        meta="Who uses Lens, how often, and which features: worked out on this server"
        actions={<SegmentedChoice label="Period" value={days} onChange={setDays} options={PERIODS} />}
        className="mb-0"
      />
      <UsageNumbers days={Number(days)} />
      <section className="flex flex-col gap-2">
        <h2 className="text-[14px] font-semibold text-fg-strong">Events</h2>
        <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
          {s?.configured ? (
            <>Each day’s events are also sent to Google Analytics ({s.measurement_id}).</>
          ) : (
            <>
              Kept on this server only. To also send them to your own Google Analytics property, turn it on in{" "}
              <Link href="/settings/analytics" className="font-semibold text-fg-accent hover:underline">
                Settings → Usage analytics
              </Link>
              .
            </>
          )}
        </p>
        <EventLog />
      </section>
    </div>
  );
}
