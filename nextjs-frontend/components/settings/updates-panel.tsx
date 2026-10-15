"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Circle, Copy, LoaderCircle, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Updates } from "@/app/openapi-client";
import type { UpdateStatus, UpgradeRun } from "@/app/openapi-client/types.gen";
import { Badge, type Tone } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/states";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

const ACTIVE: UpgradeRun["state"][] = ["backing-up", "queued", "running", "rolling-back"];

const RUN: Record<UpgradeRun["state"], { label: string; tone: Tone }> = {
  "backing-up": { label: "Backing up", tone: "intent" },
  queued: { label: "Waiting for the machine", tone: "intent" },
  running: { label: "Installing", tone: "intent" },
  "rolling-back": { label: "Going back", tone: "gate" },
  done: { label: "Installed", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  "rolled-back": { label: "Went back", tone: "gate" },
};

type StepState = "pending" | "running" | "done" | "failed";
const STEPS = [
  { id: "backup", label: "Back up the database" },
  { id: "download", label: "Download the new version" },
  { id: "build", label: "Build it" },
  { id: "start", label: "Restart Lens" },
  { id: "check", label: "Check that it answers" },
] as const;

/** Where each step is, from the run's state and the step the machine says it is on. */
export function steps(run: UpgradeRun): StepState[] {
  const at =
    run.state === "backing-up" || run.step === "backup"
      ? 0
      : run.state === "queued"
        ? 1
        : run.state === "done"
          ? STEPS.length
          : Math.max(
              1,
              STEPS.findIndex((s) => s.id === (run.step ?? "download")),
            );
  const ended = run.state === "failed" || run.state === "rolled-back" || run.state === "rolling-back";
  return STEPS.map((_, i) => {
    if (i < at) return "done";
    if (i > at) return "pending";
    if (ended || run.error) return "failed";
    return run.state === "queued" ? "pending" : "running";
  });
}

export const UPDATES_KEY = ["admin-updates"];

/** The update status, kept fresh while an upgrade runs (the API restarts in between: a failed poll just waits). */
export function useUpdates(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: UPDATES_KEY,
    queryFn: () => data(Updates.getUpdates({ client })),
    enabled,
    retry: false,
    refetchInterval: (q) => {
      const s = q.state.data as UpdateStatus | undefined;
      return s?.upgrade && ACTIVE.includes(s.upgrade.state) && !s.upgrade.error ? 3_000 : 10 * 60_000;
    },
  });
}

function when(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "never";
}

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Please try again.";
}

/** Settings → Updates: the version running, the latest release, one-click upgrade, and the backups. */
export function UpdatesPanel() {
  const client = useApiClient();
  const qc = useQueryClient();
  const status = useUpdates();
  const s = status.data;
  const [copied, setCopied] = useState(false);
  const refresh = (d: UpdateStatus) => qc.setQueryData(UPDATES_KEY, d);
  const check = useMutation({ mutationFn: () => data(Updates.checkUpdates({ client })), onSuccess: refresh });
  const upgrade = useMutation({
    mutationFn: () => data(Updates.startUpgrade({ client, body: { ref: s?.latest?.tag ?? null, backup: true } })),
    onSuccess: refresh,
  });
  const run = s?.upgrade;
  const busy = Boolean(run && ACTIVE.includes(run.state) && !run.error);

  if (status.isPending) return <Skeleton className="h-24 w-full" />;
  const restarting = status.isError && busy;
  if (!s)
    return (
      <Banner tone={busy ? "info" : "error"} title="Lens isn’t answering right now.">
        If it’s installing an update, it comes back on its own in a minute or two.
      </Banner>
    );

  return (
    <div className="flex flex-col gap-4">
      {s.available && s.latest ? (
        <Banner tone="info" title={`Lens ${s.latest.tag} is out. This is ${s.current}.`}>
          <a href={s.latest.url} target="_blank" rel="noreferrer" className="underline">
            Release notes on GitHub
          </a>
        </Banner>
      ) : (
        <Banner tone="success" title={`Lens ${s.current} is the latest.`}>
          {s.error ? s.error : `Last checked ${when(s.checked_at)}.`}
        </Banner>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {s.available && s.agent && (
          <Button onClick={() => upgrade.mutate()} disabled={busy || upgrade.isPending}>
            {busy ? "Installing…" : `Back up and install ${s.latest?.tag}`}
          </Button>
        )}
        <Button variant="secondary" onClick={() => check.mutate()} disabled={check.isPending}>
          <RefreshCw aria-hidden className={check.isPending ? "animate-spin" : undefined} />
          Check now
        </Button>
      </div>
      {upgrade.error && <p className="text-[12.5px] text-red-dark">{errText(upgrade.error)}</p>}
      {check.error && <p className="text-[12.5px] text-red-dark">{errText(check.error)}</p>}
      {s.available && !s.agent && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[13px] text-fg-muted">
            This machine can’t install it from here (it wasn’t installed with the install line). Run this on it once,
            and updates work from here after that:
          </p>
          <div className="flex items-start gap-2">
            <code className="flex-1 break-all rounded-sm bg-surface-neutral px-2 py-1.5 font-mono text-[12px]">
              {s.manual}
            </code>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void navigator.clipboard?.writeText(s.manual);
                setCopied(true);
              }}
            >
              <Copy aria-hidden />
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      )}
      {restarting && (
        <Banner tone="info" title="Lens is restarting on the new version.">
          This page follows along on its own: there is nothing to do.
        </Banner>
      )}
      {run && <RunView run={run} />}
      {s.available && s.latest?.notes && (
        <details className="rounded-md border border-border px-3 py-2">
          <summary className="cursor-pointer text-[13.5px] font-bold">What’s new in {s.latest.tag}</summary>
          <pre className="mt-2 whitespace-pre-wrap font-sans text-[12.5px] text-fg-muted">{s.latest.notes}</pre>
        </details>
      )}
      <p className="border-t border-border pt-4 text-[13px] text-fg-muted">
        Lens backs the database up before each upgrade. Backups, and restoring one, are in{" "}
        <Link href="/settings/backups" className="text-fg-accent underline">
          Settings → Backups
        </Link>
        .
      </p>
    </div>
  );
}

function RunView({ run }: { run: UpgradeRun }) {
  const st = RUN[run.state];
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
        <span className="font-bold">Upgrade to {run.ref}</span>
        <Badge tone={run.error ? "red" : st.tone}>{st.label}</Badge>
        <span className="text-fg-muted">
          {run.by === "auto_install" ? "by itself" : run.by ? `by ${run.by}` : ""} · {when(run.asked_at)}
        </span>
      </div>
      <ol className="flex flex-col gap-1" aria-label="Upgrade steps">
        {steps(run).map((st, i) => (
          <li key={STEPS[i].id} className="flex items-center gap-2 text-[13px]" data-state={st}>
            {st === "done" ? (
              <Check aria-hidden className="size-4 text-green-dark" />
            ) : st === "running" ? (
              <LoaderCircle aria-hidden className="size-4 animate-spin text-fg-accent" />
            ) : st === "failed" ? (
              <X aria-hidden className="size-4 text-red-dark" />
            ) : (
              <Circle aria-hidden className="size-4 text-fg-muted" />
            )}
            <span className={st === "pending" ? "text-fg-muted" : "text-fg"}>{STEPS[i].label}</span>
          </li>
        ))}
      </ol>
      {run.state === "rolling-back" && (
        <p className="text-[12.5px] text-fg-muted">
          The new version didn’t start, so Lens is going back to the version it ran before.
        </p>
      )}
      {run.error && <p className="text-[12.5px] text-red-dark">{run.error}</p>}
      {run.restore && <p className="text-[12.5px] text-fg-muted">After going back, {run.restore}.</p>}
      {run.backup && <p className="text-[12.5px] text-fg-muted">Backed up first: {run.backup}</p>}
      {run.log && run.log.length > 0 && (
        <pre className="max-h-48 overflow-auto rounded-sm bg-surface-neutral px-2 py-1.5 font-mono text-[11.5px]">
          {run.log.join("\n")}
        </pre>
      )}
    </div>
  );
}
