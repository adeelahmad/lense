"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { Updates } from "@/app/openapi-client";
import type { InstallStatus, UpgradeRun } from "@/app/openapi-client/types.gen";
import { ChoiceCards } from "@/components/settings/controls";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";

// How install.sh set this machine up, changed from Settings: the machine's upgrade agent runs install.sh again for the
// version running with the change, and Lens restarts (docs/updates.md).

export type Edition = "full" | "lean";
export type InstallChange = { public: boolean } | { target: Edition };

const KEY = ["install"];
const REACH = "Reachable from other devices on this network";
const UNKNOWN = "Lens can’t tell: it wasn’t started by install.sh or Docker Compose.";
const EDITIONS: { value: Edition; label: string; hint: string }[] = [
  { value: "full", label: "Full", hint: "Reads Office files, web pages and emails too, with LibreOffice and Chromium" },
  { value: "lean", label: "Lean", hint: "Smaller, without LibreOffice and Chromium, for a small machine" },
];

/** A change the machine is still making: waiting for its upgrade agent, or install.sh running. */
export function going(run: UpgradeRun | null | undefined): boolean {
  return Boolean(run && !run.error && (run.state === "queued" || run.state === "running"));
}

/** The install line that makes a change by hand, for a machine with no upgrade agent. */
export function manualLine(s: Pick<InstallStatus, "manual">, change: InstallChange): string {
  if ("public" in change) return change.public ? s.manual.public : s.manual.private;
  return s.manual[change.target];
}

/** Whether this page was opened at an address other than this machine's own name, so maybe over the network. */
export function onTheNetwork(hostname: string): boolean {
  const h = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return !(h === "" || h === "localhost" || h.endsWith(".localhost") || h === "::1" || /^127\./.test(h));
}

/** What a run changes, in a few words. */
export function runWhat(run: Pick<UpgradeRun, "public" | "target">): string {
  const parts: string[] = [];
  if (run.public != null) parts.push(run.public ? "opening Lens to the network" : "keeping Lens to this machine");
  if (run.target) parts.push(`switching to the ${run.target} edition`);
  const what = parts.join(" and ");
  return what.charAt(0).toUpperCase() + what.slice(1);
}

/** Where the last run is: waiting for the machine, setting Lens up again, or why it stopped. */
function RunState({ run }: { run: UpgradeRun }) {
  const log = (run.log ?? []).length > 0 && (
    <pre className="mt-2 max-h-40 overflow-auto rounded-sm bg-surface-neutral p-2 font-mono text-[11.5px]">
      {(run.log ?? []).join("\n")}
    </pre>
  );
  if (going(run))
    return (
      <Banner title={`${runWhat(run)}.`}>
        {run.state === "queued"
          ? "Waiting for the machine to pick it up."
          : "Lens is setting itself up again and restarts; this page follows when it’s back."}
        {log}
      </Banner>
    );
  if (run.state === "failed" || run.error)
    return (
      <Banner tone="error" title="The machine couldn’t change it.">
        {run.error}
        {log}
      </Banner>
    );
  return null;
}

/** The status, followed while the machine changes it (Lens is away while it restarts: the polling carries on). */
export function useInstall() {
  const client = useApiClient();
  return useQuery({
    queryKey: KEY,
    queryFn: () => data(Updates.getInstall({ client })),
    refetchInterval: (q) => (going(q.state.data?.reconfigure) ? 3000 : false),
  });
}

/** What a change does, then the one button that asks the machine for it; with no agent, the line to run there. */
function Apply({
  s,
  change,
  title,
  action,
  onDone,
  children,
}: {
  s: InstallStatus;
  change: InstallChange;
  title: string;
  action: string;
  onDone: () => void;
  children: ReactNode;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const apply = useMutation({
    mutationFn: () => data(Updates.setInstall({ client, body: change })),
    onSuccess: (got) => {
      qc.setQueryData(KEY, got);
      onDone();
    },
  });
  return (
    <Banner tone="warning" title={title}>
      {children}
      {s.agent ? (
        <span className="mt-2 block">
          <Button size="sm" variant="primary" onClick={() => apply.mutate()} disabled={apply.isPending}>
            {action}
          </Button>
        </span>
      ) : (
        <>
          <span className="mt-2 block">
            This machine has no upgrade agent to do it (Lens wasn’t installed with install.sh, or it couldn’t set one
            up). Run this on the machine Lens runs on:
          </span>
          <pre className="mt-2 overflow-auto rounded-sm bg-surface-neutral p-2 font-mono text-[11.5px]">
            {manualLine(s, change)}
          </pre>
        </>
      )}
      {apply.isError && <span className="mt-2 block text-red-dark">{apply.error.message}</span>}
    </Banner>
  );
}

/** Settings › Access & embedding: whether other devices on the network reach the web app, or this machine only. */
export function NetworkReach() {
  const q = useInstall();
  const [want, setWant] = useState<boolean | null>(null);
  if (q.isPending) return <Skeleton className="h-14" />;
  if (!q.data) return <Banner tone="error">{q.error?.message}</Banner>;
  const s = q.data;
  const run = s.reconfigure?.public != null ? s.reconfigure : null;
  const busy = going(s.reconfigure);
  const host = typeof window === "undefined" ? "" : window.location.hostname;
  const now =
    s.public == null
      ? UNKNOWN
      : s.public
        ? "Other devices here open Lens at this machine’s address. Passkeys need https:// there (Settings › HTTPS); until then people sign in on them with a sign-in link."
        : "Only this machine opens Lens. HTTPS and Remote access, when on, reach it either way.";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-5">
        <div className="flex flex-col gap-1">
          <span className="text-[14px] font-bold leading-tight text-fg">{REACH}</span>
          <span className="text-[13px] leading-[1.45] text-fg-secondary">{now}</span>
        </div>
        <span className="shrink-0 pt-0.5">
          <Switch
            aria-label={REACH}
            checked={want ?? s.public ?? false}
            disabled={busy}
            onCheckedChange={(v) => setWant(v === s.public ? null : v)}
          />
        </span>
      </div>
      {want !== null &&
        !busy &&
        (want ? (
          <Apply
            s={s}
            change={{ public: true }}
            title="Lens restarts to listen on the network."
            action="Open to the network and restart"
            onDone={() => setWant(null)}
          >
            Other devices on this network can then open it, and its sign-in page. Sensors, file shares and Grafana, when
            they’re on, listen there too.
          </Apply>
        ) : (
          <Apply
            s={s}
            change={{ public: false }}
            title="Lens restarts to listen on this machine only."
            action="Keep to this machine and restart"
            onDone={() => setWant(null)}
          >
            Other devices on this network can’t open it then.
            {onTheNetwork(host) && (
              <>
                {" "}
                You opened this page at <b className="font-mono">{host}</b>: if that’s this machine’s address on the
                network, this browser can’t reach Lens there after the restart.
              </>
            )}
          </Apply>
        ))}
      {run && <RunState run={run} />}
    </div>
  );
}

/** Settings › Components: the edition, full (LibreOffice and Chromium) or lean. */
export function EditionChoice() {
  const q = useInstall();
  const [want, setWant] = useState<Edition | null>(null);
  if (q.isPending) return <Skeleton className="h-20" />;
  if (!q.data) return <Banner tone="error">{q.error?.message}</Banner>;
  const s = q.data;
  const run = s.reconfigure?.target ? s.reconfigure : null;
  const busy = going(s.reconfigure);
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[13px] font-bold leading-tight text-fg-strong">Edition</span>
      <ChoiceCards
        label="Edition"
        options={EDITIONS}
        value={want ?? s.target ?? ""}
        onChange={(v) => setWant(v === s.target ? null : (v as Edition))}
        disabled={busy}
        columns={2}
        size="sm"
        className="max-sm:!grid-cols-1"
      />
      {s.target == null && <span className="text-[12.5px] text-fg-muted">{UNKNOWN}</span>}
      {want !== null &&
        !busy &&
        (want === "lean" ? (
          <Apply
            s={s}
            change={{ target: "lean" }}
            title="Lens restarts as the lean edition."
            action="Switch to lean and restart"
            onDone={() => setWant(null)}
          >
            It’s set up again without LibreOffice and Chromium, which can take a few minutes. PDFs and images are read
            as before; other documents then need anytopdf or Tika (Settings › Documents).
          </Apply>
        ) : (
          <Apply
            s={s}
            change={{ target: "full" }}
            title="Lens restarts as the full edition."
            action="Switch to full and restart"
            onDone={() => setWant(null)}
          >
            It’s set up again with LibreOffice and Chromium, to read Office files, web pages and emails too. That takes
            more disk, and can take a few minutes.
          </Apply>
        ))}
      {run && <RunState run={run} />}
    </div>
  );
}
