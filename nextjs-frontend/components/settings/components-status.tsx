"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Admin } from "@/app/openapi-client";
import type { Component, ComponentState, Machine, WorkerComponents } from "@/app/openapi-client/types.gen";
import type { BodyCtx } from "@/components/settings/bodies";
import { Badge, type Tone } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

/** One line about a machine: processors, memory, GPU and free disk. */
export function machineLine(m: Machine | null | undefined): string {
  if (!m) return "not reported yet";
  const gpu = m.gpus?.length ? m.gpus.map((g) => g.name).join(", ") : m.apple_silicon ? "Apple silicon" : "no GPU";
  const parts = [`${m.cpus} CPUs`, m.memory_gb != null ? `${m.memory_gb} GB memory` : null, gpu];
  if (m.disk_free_gb != null) parts.push(`${m.disk_free_gb} GB free`);
  return parts.filter(Boolean).join(" · ");
}

const STATE: Record<ComponentState["state"], { label: string; tone: Tone }> = {
  ready: { label: "Ready", tone: "green" },
  waiting: { label: "Waiting", tone: "intent" },
  fetching: { label: "Fetching", tone: "intent" },
  later: { label: "On first use", tone: "gate" },
  failed: { label: "Failed", tone: "red" },
  missing: { label: "Missing", tone: "gate" },
};

function busy(workers: WorkerComponents[]) {
  return workers.some((w) =>
    Object.values(w.components ?? {}).some((c) => c.state === "waiting" || c.state === "fetching"),
  );
}

function Where({ c, workers }: { c: Component; workers: WorkerComponents[] }) {
  if (c.kind === "program") {
    return c.here ? (
      <Badge tone="green">Here</Badge>
    ) : (
      <span className="flex flex-col items-end gap-0.5">
        <Badge tone={c.needed ? "gate" : "neutral"}>Not here</Badge>
        {c.hint && <span className="text-[11.5px] text-fg-muted">{c.hint}</span>}
      </span>
    );
  }
  const states = workers
    .map((w) => ({ w: w.name, s: w.components?.[c.id] }))
    .filter((x): x is { w: string; s: ComponentState } => Boolean(x.s));
  if (!states.length)
    return <span className="text-[12px] text-fg-muted">{c.needed ? "No worker needs it" : "Not needed"}</span>;
  return (
    <span className="flex flex-col items-end gap-0.5">
      {states.map(({ w, s }) => (
        <span key={w} className="flex items-center gap-1.5" title={s.error ?? s.detail ?? undefined}>
          {workers.length > 1 && <span className="text-[11.5px] text-fg-muted">{w}</span>}
          <Badge tone={STATE[s.state].tone}>{STATE[s.state].label}</Badge>
        </span>
      ))}
      {states.map(({ w, s }) =>
        s.detail || s.error ? (
          <span
            key={`${w}-d`}
            className="max-w-[320px] truncate text-[11.5px] text-fg-muted"
            title={s.error ?? s.detail ?? ""}
          >
            {s.error ?? s.detail}
          </span>
        ) : null,
      )}
    </span>
  );
}

/** What Lens fetches for itself and where each worker is with it; optional ones can be added. */
export function ComponentsStatus({ ctx }: { ctx: BodyCtx }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["components"],
    queryFn: () => data(Admin.listComponents({ client })),
    refetchInterval: (query) => (query.state.data && busy(query.state.data.workers) ? 4000 : 30_000),
  });
  const check = useMutation({
    mutationFn: () => data(Admin.checkComponents({ client })),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["components"] }), 1500),
  });
  const also = ctx.state("components.also");
  const extra = (also.value as string[]) ?? [];
  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <Banner tone="error">{q.error.message}</Banner>;
  const { components, workers, recommended } = q.data;
  const shown = components.filter(
    (c) => c.needed || c.optional || c.kind === "program" || workers.some((w) => w.components?.[c.id]),
  );
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1 text-[13px]">
        {(workers.length ? workers : [{ name: "this server", machine: q.data.machine } as WorkerComponents]).map(
          (w) => (
            <span key={w.name}>
              <b className="text-fg-strong">{w.name}</b>{" "}
              <span className="text-fg-secondary">{machineLine(w.machine)}</span>
            </span>
          ),
        )}
        {recommended.engine ? (
          <span className="text-fg-muted">Suits this machine for transcription: {String(recommended.engine)}</span>
        ) : null}
      </div>
      <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
        {shown.map((c) => (
          <li key={c.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2.5">
            <span className="flex min-w-0 flex-col">
              <span className="flex items-center gap-2 text-[13px] font-semibold text-fg">
                {c.optional && (
                  <Switch
                    aria-label={`Fetch ${c.label}`}
                    checked={extra.includes(c.id)}
                    onCheckedChange={(on) => also.onChange(on ? [...extra, c.id] : extra.filter((x) => x !== c.id))}
                  />
                )}
                {c.label}
                {c.license && <span className="text-[11px] font-medium text-gold-dark">{c.license}</span>}
              </span>
              <span className="text-[12px] text-fg-secondary">
                {c.purpose}
                {c.size_mb
                  ? ` · about ${c.size_mb >= 1000 ? `${(c.size_mb / 1000).toFixed(1)} GB` : `${c.size_mb} MB`}`
                  : ""}
              </span>
            </span>
            <Where c={c} workers={workers} />
          </li>
        ))}
      </ul>
      <div>
        <Button type="button" variant="secondary" size="sm" onClick={() => check.mutate()} disabled={check.isPending}>
          <RefreshCw className="size-3.5" /> Check again now
        </Button>
      </div>
    </div>
  );
}
