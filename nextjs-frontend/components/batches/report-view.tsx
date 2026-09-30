"use client";

import { Download, FileText } from "lucide-react";
import Link from "next/link";

import type { BatchReportRef } from "@/app/openapi-client/types.gen";
import { shortTitle } from "@/components/chat/cite";
import { RichText } from "@/components/chat/rich-text";
import { useBatch } from "@/components/batches/data";
import { progressLabel, progressParts } from "@/components/batches/format";
import { hoursShort } from "@/components/chat/scope";
import { useRecordingIndex } from "@/components/search/data";
import { recordingHref } from "@/components/search/links";
import { Button } from "@/components/ui/button";
import { EmptyState, Progress, Skeleton } from "@/components/ui/states";
import { plural, shortDate } from "@/lib/format";

function RefChip({ r }: { r: BatchReportRef | undefined; n: number }) {
  if (!r) return null;
  return (
    <Link
      href={recordingHref(r.recording_id)}
      className="mx-0.5 inline-flex h-[22px] items-center whitespace-nowrap rounded-pill border border-blue-border bg-blue-surface px-2 align-[2px] font-sans text-[11.5px] font-semibold text-fg-accent hover:border-blue"
    >
      {shortTitle(r.title)}
      {r.date ? ` · ${shortDate(r.date)}` : ""}
    </Link>
  );
}

/** CR1 result page: the combined report, citing each recording it draws on. */
export function ReportView({ id }: { id: number }) {
  const batch = useBatch(id);
  const index = useRecordingIndex();
  const b = batch.data;
  if (batch.isLoading)
    return (
      <div className="mx-auto flex max-w-[860px] flex-col gap-3 px-4 py-6" aria-busy="true" aria-label="Loading report">
        <Skeleton className="h-7 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  if (!b)
    return (
      <div className="px-4 py-6">
        <EmptyState tone="error" title="This report isn’t here">
          {batch.error?.message}
        </EmptyState>
      </div>
    );
  const report = b.report;
  if (!report) {
    const parts = progressParts(b.progress);
    return (
      <div className="mx-auto flex max-w-[560px] flex-col gap-4 px-4 py-10">
        <EmptyState
          icon={<FileText />}
          title={b.status === "finished" ? "Not combined yet" : "Still reading the recordings"}
          actions={
            <Button asChild variant="secondary">
              <Link href={`/batches/${id}`}>Open the batch run</Link>
            </Button>
          }
        >
          {b.status === "finished" ? "Combine the results from the batch run to write the report." : `Reading… ${progressLabel(parts)}, then combining.`}
        </EmptyState>
        {b.status !== "finished" && <Progress value={parts.total ? (parts.done + parts.failed) / parts.total : 0} label="Reading progress" />}
      </div>
    );
  }
  const refs = new Map((report.refs ?? []).map((r) => [r.n, r]));
  const hours = b.recordings.reduce((a, rid) => a + (index.byId.get(rid)?.duration_ms ?? 0), 0);
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([`# ${b.label}\n\n${report.text}\n`], { type: "text/markdown" }));
    a.download = `report-${id}.md`;
    a.click();
  };
  return (
    <div className="mx-auto flex max-w-[860px] flex-col gap-4 px-4 py-6 md:px-6">
      <Link href={`/batches/${id}`} className="w-fit text-[13px] font-semibold text-fg-secondary hover:text-fg">
        ← Batch run #{id}
      </Link>
      <article className="flex flex-col gap-4 rounded-lg border border-border p-6">
        <header className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-[20px] font-bold leading-tight text-fg">{report.instructions && report.instructions.length < 90 ? report.instructions : b.label}</h1>
            <p className="m-0 mt-1 text-[12.5px] text-fg-muted">
              {plural(b.recordings.length, "recording")}
              {hours ? ` · ${hoursShort(hours)}` : ""} · {b.label} → combined · {shortDate(report.at)}
            </p>
          </div>
          <Button size="sm" variant="secondary" icon={<Download />} onClick={download}>
            Export
          </Button>
        </header>
        <div className="font-serif text-[16px] leading-[1.6] text-fg">
          <RichText text={report.text} renderCite={(n, key) => <RefChip key={key} n={n} r={refs.get(n)} />} headingClassName="text-[15px] pt-2" />
        </div>
      </article>
    </div>
  );
}
