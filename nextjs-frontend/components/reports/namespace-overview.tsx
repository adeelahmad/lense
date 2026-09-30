"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Download, FileText, Link2, Printer } from "lucide-react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useEffect, useMemo, useState } from "react";

import { Entities, Recordings, Speakers } from "@/app/openapi-client";
import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { statusView, totalDuration } from "@/components/library/model";
import { MonthBars, SpeakerBars } from "@/components/reports/month-bars";
import {
  RANGE_LABEL,
  cloudSizes,
  isoDay,
  monthlyBuckets,
  overview,
  rangeBounds,
  rangeLabel,
  type ReportRange,
} from "@/components/reports/model";
import {
  downloadHtml,
  fetchReportHtml,
  lightOnPrint,
  openHtml,
  prepareReportHtml,
  printInLight,
} from "@/components/reports/report-html";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, shortDate, tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const MAX_PAGES = 10;

/** Every recording of a namespace (1,000 per request), for the monthly numbers. */
function useNamespaceRecordings(ns: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["recordings", "report", ns],
    queryFn: async () => {
      const out: RecordingSummary[] = [];
      for (let page = 0; page < MAX_PAGES; page++) {
        const rows = await data(
          Recordings.listRecordings({
            client,
            query: { ns, limit: 1000, offset: page * 1000 },
          }),
        );
        out.push(...rows);
        if (rows.length < 1000) break;
      }
      return out;
    },
    staleTime: 60_000,
  });
}

function Kpi({ value, label, loading }: { value: string; label: string; loading?: boolean }) {
  return (
    <div className="flex flex-col gap-[5px] rounded-md bg-surface p-3.5">
      {loading ? (
        <Skeleton className="h-6 w-16" />
      ) : (
        <b className="text-[24px] font-bold leading-none text-fg">{value}</b>
      )}
      <span className="text-[12.5px] leading-tight text-fg-muted">{label}</span>
    </div>
  );
}

/** Reports RP1: a namespace at a glance — numbers, months, speakers, entities — printable, with the HTML report. */
export function NamespaceOverview({ ns, onNamespace }: { ns: string; onNamespace: (ns: string) => void }) {
  const client = useApiClient();
  const token = useSession().data?.accessToken;
  const toast = useToast();
  const { namespaces } = useArchive();
  const [range, setRange] = useState<ReportRange>("6m");
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);
  useEffect(() => lightOnPrint(), []);

  const recs = useNamespaceRecordings(ns);
  const oldest = recs.data?.length ? recs.data[recs.data.length - 1].recorded_at : null;
  const bounds = useMemo(() => (now ? rangeBounds(range, now, oldest) : null), [range, now, oldest]);
  const speakers = useQuery({
    queryKey: ["speakers", ns],
    queryFn: () => data(Speakers.listSpeakers({ client, query: { ns } })),
    staleTime: 60_000,
  });
  const entities = useQuery({
    queryKey: ["entities", "report", ns, bounds?.from?.toISOString() ?? "all"],
    queryFn: () =>
      data(
        Entities.listEntities({
          client,
          query: {
            namespaces: ns,
            limit: 10,
            sort: "mentions",
            ...(bounds?.from ? { date_from: isoDay(bounds.from), date_to: isoDay(bounds.to) } : {}),
          },
        }),
      ),
    enabled: Boolean(bounds),
    staleTime: 60_000,
  });

  const months = useMemo(() => (bounds && recs.data ? monthlyBuckets(recs.data, bounds) : []), [bounds, recs.data]);
  const totals = useMemo(() => (bounds && recs.data ? overview(recs.data, bounds) : null), [bounds, recs.data]);
  const top = (speakers.data?.speakers ?? [])
    .map((s) => ({ name: s.display, ms: s.talk_ms ?? 0 }))
    .filter((s) => s.ms > 0)
    .sort((a, b) => b.ms - a.ms)
    .slice(0, 5);
  const cloud = cloudSizes(
    (entities.data?.items ?? [])
      .map((i) => ({
        name: String(i.name ?? ""),
        mentions: Number(i.mentions ?? 0),
      }))
      .filter((i) => i.name),
  );
  const inRange = (recs.data ?? []).filter(
    (r) => bounds && r.recorded_at && Date.parse(r.recorded_at) >= (bounds.from?.getTime() ?? 0),
  );

  const html = async (mode: "open" | "download") => {
    try {
      const page = await fetchReportHtml(`/reports/${encodeURIComponent(ns)}/index.html`, token);
      const ready = prepareReportHtml(page, {
        scripts: true,
        origin: window.location.origin,
      });
      if (mode === "open") openHtml(ready);
      else downloadHtml(ready, `${ns}-report.html`);
    } catch (e) {
      toast({
        title: "No HTML report",
        body: (e as Error).message,
        tone: "red",
      });
    }
  };

  const loading = recs.isLoading || !bounds;
  return (
    <div
      id="report-print"
      className="report-print flex flex-col gap-[18px] rounded-md border border-border bg-background px-4 py-5 md:px-6 md:py-[22px]"
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="min-w-0 flex-1 text-[22px] font-bold leading-tight text-fg">
          <Menu>
            <MenuTrigger
              className="inline-flex max-w-full items-center gap-1 rounded-sm hover:bg-surface-neutral print:hidden"
              aria-label={`Namespace: ${ns}. Change`}
            >
              <span className="truncate">{ns}</span>
              <ChevronDown className="size-4 shrink-0 text-fg-muted" aria-hidden />
            </MenuTrigger>
            <MenuContent align="start">
              <MenuLabel>Namespace</MenuLabel>
              {namespaces.map((n) => (
                <MenuItem key={n.name} onSelect={() => onNamespace(n.name)}>
                  {n.name}
                </MenuItem>
              ))}
            </MenuContent>
          </Menu>
          <span className="hidden print:inline">{ns}</span> · overview
        </h2>
        <Menu>
          <MenuTrigger className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-border px-3 text-[13px] font-medium text-fg hover:bg-surface print:hidden">
            {bounds ? rangeLabel(bounds) : RANGE_LABEL[range]}
            <ChevronDown className="size-[13px]" aria-hidden />
          </MenuTrigger>
          <MenuContent align="end">
            <MenuLabel>Date range</MenuLabel>
            {(Object.keys(RANGE_LABEL) as ReportRange[]).map((r) => (
              <MenuItem key={r} onSelect={() => setRange(r)}>
                <span className={cn(r === range && "font-bold")}>{RANGE_LABEL[r]}</span>
              </MenuItem>
            ))}
          </MenuContent>
        </Menu>
        <span className="hidden text-[13px] text-fg-secondary print:inline">{bounds ? rangeLabel(bounds) : ""}</span>
        <Menu>
          <MenuTrigger asChild>
            <Button variant="secondary" size="sm" icon={<Download />} className="print:hidden">
              PDF / HTML
            </Button>
          </MenuTrigger>
          <MenuContent align="end">
            <MenuItem icon={<Printer />} onSelect={() => printInLight()}>
              Print or save as PDF
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon={<FileText />} onSelect={() => void html("open")}>
              Open the HTML report
            </MenuItem>
            <MenuItem icon={<Download />} onSelect={() => void html("download")}>
              Download HTML
            </MenuItem>
          </MenuContent>
        </Menu>
        <Button
          variant="secondary"
          size="sm"
          icon={<Link2 />}
          disabled
          disabledReason="Not available yet: share links exist for single recordings, not for a namespace report."
          className="print:hidden"
        >
          Share
        </Button>
      </div>

      {recs.isError ? (
        <EmptyState
          tone="error"
          title="Couldn’t load this namespace"
          actions={<Button onClick={() => recs.refetch()}>Try again</Button>}
        >
          {(recs.error as Error).message}
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi loading={loading} value={count(totals?.recordings)} label="recordings" />
            <Kpi
              loading={loading}
              value={totals ? totalDuration(totals.ms).replace("0 min", "0 h") : ""}
              label="of audio"
            />
            <Kpi loading={loading} value={count(totals?.speakers)} label="speakers" />
            <Kpi loading={entities.isLoading || !bounds} value={count(entities.data?.total)} label="entities" />
          </div>

          {!loading && totals?.recordings === 0 ? (
            <EmptyState title={`Nothing recorded in ${ns} in this range`}>
              {recs.data?.length
                ? "Pick a longer range to see earlier months."
                : "Recordings show up here once they’re imported and analysed."}
            </EmptyState>
          ) : (
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              {loading ? <Skeleton className="h-[200px] rounded-md" /> : <MonthBars months={months} />}
              <div className="flex flex-col gap-2">
                <h3 className="flex items-baseline gap-2 text-[13px] font-bold leading-none text-fg">
                  Top speakers
                  {range !== "all" && <span className="text-[11.5px] font-medium text-fg-muted">all time</span>}
                </h3>
                {speakers.isLoading ? (
                  <Skeleton className="w-3/4" />
                ) : top.length ? (
                  <SpeakerBars rows={top} />
                ) : (
                  <p className="text-[13px] text-fg-muted">No speaker has talk time yet.</p>
                )}
                <h3 className="mt-2.5 text-[13px] font-bold leading-none text-fg">Top entities</h3>
                {entities.isLoading ? (
                  <Skeleton className="w-2/3" />
                ) : cloud.length ? (
                  <ul className="flex flex-wrap items-baseline gap-x-3 gap-y-1" aria-label="Most mentioned entities">
                    {cloud.map((w, i) => (
                      <li
                        key={w.name}
                        className={cn(
                          "leading-[1.2]",
                          w.strong ? "font-bold" : "font-medium",
                          i % 2 ? "text-fg-secondary" : "text-fg",
                        )}
                        style={{ fontSize: w.size }}
                      >
                        {w.name}
                        <span className="sr-only"> ({w.mentions} mentions)</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[13px] text-fg-muted">No entities found in this range.</p>
                )}
              </div>
            </div>
          )}

          {inRange.length > 0 && (
            <div className="flex flex-col gap-2">
              <h3 className="text-[13px] font-bold leading-none text-fg">Recording reports</h3>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] table-fixed text-[13px]">
                  <colgroup>
                    <col />
                    <col style={{ width: 150 }} />
                    <col style={{ width: 80 }} />
                    <col style={{ width: 150 }} />
                  </colgroup>
                  <thead>
                    <tr className="border-b border-border text-left text-[12px] text-fg-secondary">
                      <th scope="col" className="py-2 font-semibold">
                        Recording
                      </th>
                      <th scope="col" className="py-2 font-semibold">
                        Date
                      </th>
                      <th scope="col" className="py-2 text-right font-semibold">
                        Length
                      </th>
                      <th scope="col" className="py-2 pl-4 font-semibold">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {inRange.slice(0, 25).map((r) => {
                      const v = statusView(r);
                      return (
                        <tr key={r.id} className="border-b border-border">
                          <td className="py-2 pr-3">
                            <Link
                              href={`/reports?recording=${r.id}`}
                              className="block truncate font-semibold text-fg hover:underline"
                            >
                              {r.title || "Untitled"}
                            </Link>
                          </td>
                          <td className="tabular whitespace-nowrap py-2 text-fg-secondary">
                            {shortDate(r.recorded_at)}
                          </td>
                          <td className="tabular py-2 text-right text-fg-secondary">
                            {r.duration_ms ? tc(r.duration_ms) : "—"}
                          </td>
                          <td className="py-2 pl-4">
                            <Badge tone={v.tone} dot>
                              {v.label}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {inRange.length > 25 && (
                <p className="text-[12.5px] text-fg-muted">
                  The 25 most recent of {count(inRange.length)} in this range. The Library lists them all.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
