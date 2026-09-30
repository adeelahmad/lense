"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Cpu, Database, HardDriveDownload, KeyRound, Sparkles, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Admin, Jobs } from "@/app/openapi-client";
import { AdminFrame } from "@/components/admin/admin-frame";
import { isUnreachable, ServerUnreachable } from "@/components/errors/error-states";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { count, relative } from "@/lib/format";
import { jobSteps } from "@/lib/hooks/jobs";
import { cn } from "@/lib/utils";

/** A worker that hasn't sent a heartbeat for this long is treated as silent. */
export const SILENT_MS = 2 * 60_000;
const REFRESH_MS = 30_000;

type Tone = "ok" | "warn" | "bad" | "idle";
type Worker = {
  name: string;
  host?: string;
  steps?: string[];
  heartbeat_at?: string;
  current?: unknown;
};

const TONE: Record<Tone, { box: string; glyph: string; color: string }> = {
  ok: { box: "border-border bg-background", glyph: "✓", color: "text-green" },
  warn: {
    box: "border-gold-border bg-gold-surface",
    glyph: "◆",
    color: "text-gold-dark",
  },
  bad: {
    box: "border-red-border bg-red-surface",
    glyph: "✕",
    color: "text-red",
  },
  idle: {
    box: "border-border bg-background",
    glyph: "–",
    color: "text-fg-muted",
  },
};

function Card({
  icon: Icon,
  name,
  tone,
  status,
  children,
  link,
}: {
  icon: LucideIcon;
  name: string;
  tone: Tone;
  status: ReactNode;
  children?: ReactNode;
  link?: ReactNode;
}) {
  const t = TONE[tone];
  return (
    <section aria-label={name} className={cn("flex flex-col gap-2.5 rounded-lg border p-4", t.box)}>
      <div className="flex items-center gap-2">
        <Icon aria-hidden className="size-[17px] text-fg-secondary" />
        <b className="flex-1 text-[14px] font-bold leading-none">{name}</b>
      </div>
      <div className="flex items-center gap-[7px] text-[15px] font-bold leading-[1.2]">
        <span aria-hidden className={cn("font-extrabold", t.color)}>
          {t.glyph}
        </span>
        {status}
      </div>
      {children && <div className="text-[12.5px] leading-[1.45] text-fg-secondary">{children}</div>}
      {link && <div className="mt-auto text-[12.5px] font-semibold">{link}</div>}
    </section>
  );
}

/** Whether each worker is alive, by its last heartbeat. */
export function aliveWorkers(workers: Worker[], now = Date.now()): Worker[] {
  return workers.filter((w) => w.heartbeat_at && now - Date.parse(w.heartbeat_at) < SILENT_MS);
}

/** Queued jobs whose next step no live worker can run. */
export function waitingForWorker(jobs: { next_step?: string | null; steps?: unknown[] }[], alive: Worker[]): number {
  const can = new Set(alive.flatMap((w) => w.steps ?? []));
  return jobs.filter((j) => {
    const step = j.next_step ?? jobSteps(j.steps)[0];
    return step && !can.has(step);
  }).length;
}

/** System health (Admin AD5): database, workers, sources, credentials, LLM, disk and the queue, refreshed every 30 s. */
export function HealthPage() {
  const client = useApiClient();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => data(Admin.getHealth({ client })),
    refetchInterval: REFRESH_MS,
  });
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: async () =>
      (await data(Admin.getSettings({ client }))) as unknown as Record<string, { values: Record<string, unknown> }>,
  });
  const queued = useQuery({
    queryKey: ["jobs", "queued-health"],
    queryFn: () => data(Jobs.listJobs({ client, query: { status: "queued", limit: 500 } })),
    refetchInterval: REFRESH_MS,
  });
  const test = useMutation({
    mutationFn: () => data(Admin.testLlm({ client })),
  });

  if (health.isPending)
    return (
      <AdminFrame tab="health" title="System health">
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-36 rounded-lg" />
          ))}
        </div>
      </AdminFrame>
    );
  if (health.isError)
    return (
      <AdminFrame tab="health" title="System health">
        {isUnreachable(health.error) ? (
          <ServerUnreachable
            error={health.error}
            onRetry={() => health.refetch()}
            className="rounded-md border border-border"
          />
        ) : (
          <EmptyState
            tone="error"
            icon={<Database />}
            title="Couldn’t check the system"
            actions={<Button onClick={() => health.refetch()}>Try again</Button>}
          >
            {health.error.message}
          </EmptyState>
        )}
      </AdminFrame>
    );

  const h = health.data;
  const workers = h.workers as Worker[];
  const alive = aliveWorkers(workers);
  const failing = h.sources.filter((s) => s.health && s.health.ok === false);
  const unchecked = h.sources.filter((s) => !s.health);
  const llm = settings.data?.llm?.values ?? {};
  const llmSet = Boolean(llm.base_url && llm.model);
  const jobs = queued.data?.jobs ?? [];
  const oldest = jobs
    .map((j) => (j as { created_at?: string }).created_at)
    .filter(Boolean)
    .sort()[0];
  const waiting = waitingForWorker(jobs, alive);
  const q = h.queue;
  const used = Math.max(0, h.disk.total_gb - h.disk.free_gb);
  const usedPct = h.disk.total_gb ? (used / h.disk.total_gb) * 100 : 0;
  const checked = new Date(health.dataUpdatedAt).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const others = Object.entries(q).filter(([k]) => !["running", "queued", "failed"].includes(k));

  return (
    <AdminFrame
      tab="health"
      title="System health"
      meta={<span aria-live="polite">Checked {checked} · refreshes every 30 s</span>}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
        <Card
          icon={Database}
          name="Database"
          tone={h.database.ok ? "ok" : "bad"}
          status={h.database.ok ? "OK" : "Not answering"}
          link={
            <Link href="/settings/startup" className="text-fg-accent hover:underline">
              Settings → Set at startup
            </Link>
          }
        >
          SurrealDB · {h.database.embedded ? "embedded" : "server"} · full-text search{" "}
          {h.database.fulltext ? `(${h.database.fulltext})` : "unavailable"} · {count(h.counts.recordings)} recordings,{" "}
          {count(h.counts.speakers)} speakers, {count(h.counts.accounts)} accounts
        </Card>
        <Card
          icon={Cpu}
          name="Workers"
          tone={!workers.length ? "bad" : alive.length === workers.length ? "ok" : alive.length ? "warn" : "bad"}
          status={workers.length ? `${alive.length} of ${workers.length} alive` : "No workers"}
          link={
            <Link href="/activity" className="text-fg-accent hover:underline">
              Activity → Workers
            </Link>
          }
        >
          {workers.length
            ? workers
                .map(
                  (w) =>
                    `${w.name}${w.host ? ` (${w.host})` : ""} · ${w.heartbeat_at ? `heartbeat ${relative(w.heartbeat_at)}` : "no heartbeat"}`,
                )
                .join(" · ")
            : "Nothing is processing. Start a worker, or set Workers inside the server above 0."}
        </Card>
        <Card
          icon={HardDriveDownload}
          name="Sources"
          tone={failing.length ? "bad" : h.sources.length ? "ok" : "idle"}
          status={
            failing.length
              ? `${failing.length} failing`
              : h.sources.length
                ? `${h.sources.length} OK`
                : "None connected"
          }
          link={
            <Link href="/sources" className="text-fg-accent hover:underline">
              Sources
            </Link>
          }
        >
          {failing.length
            ? failing.map((s) => `${s.name}: ${String(s.health?.error ?? "failed")}`).join(" · ")
            : unchecked.length
              ? `${unchecked.length} not checked yet`
              : h.sources.length
                ? `Checked ${relative(String(h.sources[0].health?.checked_at ?? ""))}`
                : "Recordings come only from uploads and imports."}
        </Card>
        <Card
          icon={KeyRound}
          name="Tokens"
          tone="idle"
          status="Not tracked yet"
          link={
            <Link href="/sources" className="text-fg-accent hover:underline">
              Sources
            </Link>
          }
        >
          The server doesn’t report when a source’s credentials expire yet; a failing source shows it.
        </Card>
        <Card
          icon={Sparkles}
          name="LLM provider"
          tone={!settings.data ? "idle" : !llmSet ? "warn" : test.data ? (test.data.ok ? "ok" : "bad") : "ok"}
          status={
            !settings.data ? "…" : !llmSet ? "Not set up" : test.data ? (test.data.ok ? "OK" : "Failing") : "Configured"
          }
          link={
            <span className="flex flex-wrap items-center gap-3">
              <Link href="/settings/llm" className="text-fg-accent hover:underline">
                Settings → LLM
              </Link>
              {llmSet && (
                <button
                  type="button"
                  className="text-fg-accent hover:underline disabled:opacity-50"
                  disabled={test.isPending}
                  onClick={() => test.mutate()}
                >
                  {test.isPending ? "Testing…" : "Test now"}
                </button>
              )}
            </span>
          }
        >
          {!llmSet
            ? "Summaries, templates and chat need an OpenAI-compatible model."
            : test.data
              ? test.data.ok
                ? `${test.data.model ?? String(llm.model)} answered in ${test.data.ms} ms`
                : test.data.error
              : `${String(llm.model)} at ${String(llm.base_url)}`}
        </Card>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <section aria-label="Disk" className="flex flex-col gap-2 rounded-md border border-border px-4 py-3.5">
          <b className="text-[13px] font-bold">Disk · {h.disk.path}</b>
          <div
            className="flex h-2 overflow-hidden rounded-pill bg-surface-neutral"
            role="img"
            aria-label={`${Math.round(usedPct)}% used`}
          >
            <span
              className={cn(usedPct > 90 ? "bg-red" : usedPct > 75 ? "bg-gold" : "bg-blue")}
              style={{ width: `${usedPct}%` }}
            />
          </div>
          <span className="tabular text-[12.5px] leading-[1.4] text-fg-secondary">
            {used.toFixed(1)} GB used · free {h.disk.free_gb} GB of {h.disk.total_gb} GB. The split between audio, cache
            and database isn’t reported yet.
          </span>
        </section>
        <section aria-label="Queue" className="flex flex-col gap-2 rounded-md border border-border px-4 py-3.5">
          <b className="text-[13px] font-bold">Queue</b>
          <div className="tabular flex flex-wrap gap-x-[18px] gap-y-1 text-[20px] font-bold leading-none">
            <span>
              {count(q.running ?? 0)} <span className="text-[12px] font-normal text-fg-muted">running</span>
            </span>
            <span>
              {count(q.queued ?? 0)} <span className="text-[12px] font-normal text-fg-muted">queued</span>
            </span>
            <span>
              {count(waiting)} <span className="text-[12px] font-normal text-fg-muted">waiting for a worker</span>
            </span>
            <span className={cn((q.failed ?? 0) > 0 && "text-red-dark")}>
              {count(q.failed ?? 0)} <span className="text-[12px] font-normal text-fg-muted">failed</span>
            </span>
          </div>
          <span className="text-[12.5px] leading-[1.4] text-fg-secondary">
            {oldest ? `Oldest queued: ${relative(oldest).replace(/ ago$/, "")}` : "Nothing waiting"}
            {waiting ? ` · ${waiting} need a step no live worker runs` : ""}
            {others.length ? ` · ${others.map(([k, n]) => `${count(n)} ${k}`).join(", ")} in total` : ""}
          </span>
        </section>
      </div>
    </AdminFrame>
  );
}
