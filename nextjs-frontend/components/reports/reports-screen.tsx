"use client";

import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesColumn } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";

import { Recordings } from "@/app/openapi-client";
import { NamespaceOverview } from "@/components/reports/namespace-overview";
import { RecordingReport, ReportBack } from "@/components/reports/recording-report";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const OVERVIEW_PRINT_CSS = `@media print{
  @page{size:A4;margin:14mm}
  body *{visibility:hidden!important}
  .report-print,.report-print *{visibility:visible!important}
  .report-print{position:absolute;left:0;top:0;width:100%;border:0!important}
}`;

/**
 * /reports — RP1, a namespace overview (`?ns=`, else the top bar's namespace), and RP2, a recording's printable report
 * (`?recording=`). Query parameters keep these off /reports/<ns>/…, which is where the backend serves its HTML reports.
 */
export function ReportsScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const client = useApiClient();
  const { namespaces, namespace, me } = useArchive();
  const rid = Number(params.get("recording"));
  const recording = Number.isInteger(rid) && rid > 0 ? rid : null;
  const asked = params.get("ns");
  // The asked-for namespace, else the top bar's, else the busiest one.
  const busiest = [...namespaces].sort((a, b) => (Number(b.recordings) || 0) - (Number(a.recordings) || 0))[0]?.name ?? null;
  const ns = asked && namespaces.some((n) => n.name === asked) ? asked : namespace && namespaces.some((n) => n.name === namespace) ? namespace : busiest;

  const rec = useQuery({
    queryKey: ["recording", recording],
    queryFn: () => data(Recordings.getRecording({ client, path: { rid: recording as number } })),
    enabled: recording != null,
  });

  if (recording != null) {
    return (
      <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
        <div className="print:hidden">
          <ReportBack ns={rec.data?.namespace} />
          <PageHeader title="Recording report" meta={rec.data?.title ?? undefined} className="mb-0 mt-2" />
        </div>
        <RecordingReport id={recording} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <style>{OVERVIEW_PRINT_CSS}</style>
      <PageHeader title="Reports" meta="A namespace at a glance; open a recording for its printable report." className="mb-0 print:hidden" />
      {!me || (!ns && Object.keys(me.roles ?? {}).length > 0) ? (
        <Skeleton className="h-[420px] rounded-md" />
      ) : !ns ? (
        <EmptyState icon={<ChartNoAxesColumn />} title="No namespaces yet">
          You don’t have a role in any namespace. Ask an admin to add you, and reports for it show up here.
        </EmptyState>
      ) : (
        <NamespaceOverview ns={ns} onNamespace={(n) => router.replace(`/reports?ns=${encodeURIComponent(n)}`, { scroll: false })} />
      )}
    </div>
  );
}
