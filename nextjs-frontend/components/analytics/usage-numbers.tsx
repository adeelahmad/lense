"use client";

import { useQuery } from "@tanstack/react-query";

import { Admin } from "@/app/openapi-client";
import type { AnalyticsMetrics } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

function pct(v: number | null | undefined) {
  return v == null ? "—" : `${Math.round(v * 1000) / 10}%`;
}

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-sm border border-border px-3 py-2">
      <span className="text-[12px] text-fg-muted">{label}</span>
      <span className="text-[18px] font-semibold tabular-nums text-fg-strong">{value}</span>
      {hint && <span className="text-[11.5px] text-fg-muted">{hint}</span>}
    </div>
  );
}

/** Daily active people as a row of bars, one per day. */
function DailyBars({ days }: { days: AnalyticsMetrics["daily"] }) {
  const top = Math.max(1, ...days.map((d) => d.active_users));
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12.5px] text-fg-secondary">Active people each day</span>
      <div className="flex h-16 items-end gap-[2px]" role="img" aria-label="Active people each day">
        {days.map((d) => (
          <span
            key={d.day}
            title={`${d.day}: ${d.active_users} active, ${d.actions} actions`}
            className="flex-1 rounded-t-[2px] bg-blue-dark"
            style={{
              height: `${Math.max(d.active_users ? 6 : 1, (d.active_users / top) * 100)}%`,
              opacity: d.active_users ? 1 : 0.25,
            }}
          />
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-fg-muted">
        <span>{days[0]?.day}</span>
        <span>{days[days.length - 1]?.day}</span>
      </div>
    </div>
  );
}

/** Active people, churn, retention and feature use, worked out on this server (docs/analytics.md). */
export function UsageNumbers({ days = 30 }: { days?: number }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["usage-analytics", days],
    queryFn: () => data(Admin.usageMetrics({ client, query: { days } })),
  });
  if (q.isError) return <Banner tone="error">{q.error.message}</Banner>;
  const m = q.data;
  if (!m) return <Skeleton className="h-40" />;
  const c = m.churn;
  const weeks = Math.max(0, ...m.retention.map((r) => r.retention.length));
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        The last {m.days} days ({m.since} to {m.until}), from the activity ledger and sign-ins on this server. Nothing
        here is sent anywhere.
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="Active today" value={m.dau} />
        <Tile label="Active this week" value={m.wau} />
        <Tile label="Active this month" value={m.mau} hint={`of ${m.accounts} accounts`} />
        <Tile label="Stickiness" value={pct(m.stickiness)} hint="daily ÷ monthly active" />
        <Tile
          label="Churn rate"
          value={pct(c.rate)}
          hint={c.previous_active ? `${c.churned} of ${c.previous_active} didn’t come back` : "nobody active before"}
        />
        <Tile label="Retained" value={c.retained} hint="active in both periods" />
        <Tile label="New or returning" value={c.returning} hint={`${m.new_users} new accounts`} />
        <Tile
          label="Last used"
          value={
            m.days_since_active == null ? "never" : m.days_since_active === 0 ? "today" : `${m.days_since_active}d ago`
          }
        />
      </div>
      <DailyBars days={m.daily} />
      {m.retention.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px] tabular-nums">
            <caption className="pb-1 text-left text-fg-secondary">Retention by sign-up week</caption>
            <thead>
              <tr className="text-left text-fg-muted">
                <th className="py-1 pr-3 font-normal">Week of</th>
                <th className="py-1 pr-3 font-normal">People</th>
                {Array.from({ length: weeks }, (_, i) => (
                  <th key={i} className="py-1 pr-3 font-normal">
                    W{i}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.retention.map((r) => (
                <tr key={r.week} className="border-t border-border-subtle">
                  <td className="py-1 pr-3 text-fg">{r.week}</td>
                  <td className="py-1 pr-3 text-fg">{r.users}</td>
                  {Array.from({ length: weeks }, (_, i) => (
                    <td key={i} className="py-1 pr-3 text-fg-secondary">
                      {i < r.retention.length ? pct(r.retention[i]) : ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {m.features.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[12.5px] text-fg-secondary">Features used</span>
          <ul className="flex flex-wrap gap-1.5">
            {m.features.slice(0, 16).map((f) => (
              <li
                key={f.feature}
                className="rounded-pill bg-surface-neutral px-2 py-0.5 text-[12px] text-fg"
                title={`${f.actions} actions by ${f.users} people`}
              >
                {f.feature} <span className="tabular-nums text-fg-muted">{f.actions}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
