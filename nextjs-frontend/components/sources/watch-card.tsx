"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Sources } from "@/app/openapi-client";
import type { Watch } from "@/app/openapi-client/types.gen";
import { SCAN_STATS, nextScan, watchSummary } from "@/components/sources/source-model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute } from "@/lib/format";
import { cn } from "@/lib/utils";

const hm = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const t = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toDateString() === new Date().toDateString() ? t : `${d.getDate()}/${d.getMonth() + 1} ${t}`;
};

/** A watched folder's settings and last scan: seen / new / waiting / skipped / errors, next scan, last error. */
export function WatchCard({
  watch,
  pipelineName,
  onEdit,
  manage,
  showSource,
}: {
  watch: Watch;
  pipelineName?: string | null;
  onEdit?: () => void;
  /** Admins scan and edit; owners see it read-only. */
  manage: boolean;
  showSource?: boolean;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const scan = useMutation({
    mutationFn: () => data(Sources.scanWatch({ client, path: { wid: watch.id } })),
    onSuccess: () => {
      toast({
        title: "Scanning now",
        body: "Counts update when the scan finishes.",
      });
      for (const ms of [2500, 8000, 20_000]) setTimeout(() => void qc.invalidateQueries({ queryKey: ["watches"] }), ms);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t start a scan", body: e.message }),
  });
  const stats = (watch.last_stats ?? {}) as Record<string, number>;
  const scanned = Boolean(watch.last_scan_at);
  return (
    <article
      aria-label={`Watched folder ${watch.id}`}
      className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2.5 rounded-md border border-border bg-background px-4 py-3.5"
    >
      <div className="flex min-w-0 flex-col gap-[5px]">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <code className="truncate font-mono text-[13px] font-semibold text-fg">
            {showSource && watch.source_name ? `${watch.source_name}:` : ""}
            {watch.path || "/"}
          </code>
          <span className="text-[12px] font-medium text-fg-muted">watched folder {watch.id}</span>
          {watch.enabled === false ? (
            <Badge dot>Paused</Badge>
          ) : (
            <Badge tone="green" dot>
              Enabled
            </Badge>
          )}
        </div>
        <span className="text-[12.5px] leading-snug text-fg-secondary">{watchSummary(watch, pipelineName)}</span>
      </div>
      {manage ? (
        <div className="flex items-start gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw />}
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
          >
            Scan now
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit}>
            Edit
          </Button>
        </div>
      ) : (
        <span />
      )}
      <div className="tabular col-span-2 flex flex-wrap items-center gap-x-[22px] gap-y-1 border-t border-border pt-2.5 text-[12.5px] text-fg-secondary">
        <span title={absolute(watch.last_scan_at)}>
          Last scan <b className="text-fg">{hm(watch.last_scan_at)}</b>
        </span>
        {scanned &&
          SCAN_STATS.map((s) => (
            <Tooltip key={s.key} content={s.help}>
              <span tabIndex={0}>
                {s.label}{" "}
                <b className={cn(s.key === "errors" && (stats[s.key] ?? 0) > 0 ? "text-red-dark" : "text-fg")}>
                  {stats[s.key] ?? 0}
                </b>
              </span>
            </Tooltip>
          ))}
        <span className="flex-1" />
        <span title={absolute(watch.next_scan_at)}>Next scan {nextScan(watch.next_scan_at, watch.enabled)}</span>
      </div>
      {watch.last_error && (
        <div className="col-span-2 flex gap-2 rounded-sm bg-red-surface px-2.5 py-2 text-[12.5px] leading-snug">
          <span aria-hidden className="font-extrabold text-red">
            ✕
          </span>
          <span className="min-w-0 break-words">
            Last error: <code className="font-mono">{watch.last_error}</code>
          </span>
        </div>
      )}
    </article>
  );
}
