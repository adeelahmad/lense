"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Updates } from "@/app/openapi-client";
import type { Monitoring } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Switch } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";

/** Grafana's address: the one set in Settings, else where the monitoring profile runs it (this host, its port). */
export function grafanaAddress(
  configured: string | null | undefined,
  m: Pick<Monitoring, "on" | "state" | "grafana_port"> | null | undefined,
  hostname: string,
): string | null {
  if (configured) return configured;
  if (!m?.on || m.state !== "done" || !hostname) return null;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname; // an IPv6 address
  return `http://${host}:${m.grafana_port ?? 3001}`;
}

/** What the switch says under it: waiting for the machine, starting, stopping, running or off. */
export function monitoringNote(m: Monitoring): string {
  if (m.state === "queued") return "Waiting for the machine to pick it up…";
  if (m.state === "running") return m.on ? "Starting Prometheus, node-exporter and Grafana…" : "Stopping them…";
  if (m.on && m.state === "done") return "Running: Prometheus keeps the history, Grafana shows it.";
  return "Off. On starts Prometheus, node-exporter and Grafana next to Lens (about 150-250 MB of memory).";
}

/** Whether the monitoring containers run, while the machine changes it. */
export function useMonitoring() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["monitoring"],
    queryFn: () => data(Updates.getMonitoring({ client })),
    refetchInterval: (q) => (q.state.data?.state === "queued" || q.state.data?.state === "running" ? 2000 : false),
  });
}

/** Monitoring on or off: Prometheus, node-exporter and Grafana, started by the machine's upgrade agent. */
export function MonitoringSwitch() {
  const client = useApiClient();
  const qc = useQueryClient();
  const q = useMonitoring();
  const turn = useMutation({
    mutationFn: (on: boolean) => data(Updates.setMonitoring({ client, body: { on } })),
    onSuccess: (m) => {
      qc.setQueryData(["monitoring"], m);
      void qc.invalidateQueries({ queryKey: ["settings"] }); // on turns /metrics on, with a token
    },
  });
  const m = q.data;
  if (!m) return null;
  const busy = turn.isPending || ((m.state === "queued" || m.state === "running") && !m.error);
  return (
    <div className="flex flex-col gap-2">
      <Switch
        checked={turn.isPending ? Boolean(turn.variables) : m.on}
        onCheckedChange={(on) => turn.mutate(on)}
        disabled={busy || !m.agent}
        label="Charts and history (Prometheus and Grafana)"
      />
      {m.agent ? (
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">{monitoringNote(m)}</span>
      ) : (
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">
          This machine has no upgrade agent to start them (Lens wasn’t installed with install.sh, or it couldn’t set one
          up). On the machine, run <code className="break-all font-mono">{m.manual}</code>
        </span>
      )}
      {m.error && (
        <Banner tone="error" title="The machine couldn’t change it.">
          {m.error}
        </Banner>
      )}
      {turn.isError && <Banner tone="error">{turn.error.message}</Banner>}
    </div>
  );
}
