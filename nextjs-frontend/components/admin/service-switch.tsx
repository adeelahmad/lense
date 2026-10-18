"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Updates } from "@/app/openapi-client";
import type { Service } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Switch } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";

/** What the switch says under it: waiting for the machine, starting, stopping, running, or off. */
export function serviceNote(s: Service): string {
  if (s.state === "queued") return "Waiting for the machine to pick it up…";
  if (s.state === "running") return s.on ? `Starting the ${s.label.toLowerCase()}…` : "Stopping it…";
  if (s.on && s.running === false)
    return s.state === "done" ? "On, but not answering yet: it may still be starting." : "On, but not answering.";
  if (s.on) return "Running.";
  return `Off. ${s.description}.`;
}

/** Lens's optional containers (the MCP runner, the browser, monitoring), while the machine changes one. */
export function useServices() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["services"],
    queryFn: () => data(Updates.listServices({ client })),
    refetchInterval: (q) =>
      q.state.data?.services?.some(
        (s) => s.state === "queued" || s.state === "running" || (s.on && s.state === "done" && s.running === false),
      )
        ? 2000
        : false,
  });
}

/** One optional container on or off, started and stopped by the machine's upgrade agent. */
export function ServiceSwitch({ profile, label }: { profile: string; label?: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const q = useServices();
  const turn = useMutation({
    mutationFn: (on: boolean) => data(Updates.setService({ client, path: { profile }, body: { on } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["services"] });
      void qc.invalidateQueries({ queryKey: ["mcp-presets"] });
      if (profile === "monitoring") void qc.invalidateQueries({ queryKey: ["monitoring"] });
    },
  });
  const s = q.data?.services?.find((x) => x.profile === profile);
  if (!s) return null;
  const busy = turn.isPending || ((s.state === "queued" || s.state === "running") && !s.error);
  return (
    <div className="flex flex-col gap-2">
      <Switch
        checked={turn.isPending ? Boolean(turn.variables) : s.on}
        onCheckedChange={(on) => turn.mutate(on)}
        disabled={busy || !s.agent}
        label={label ?? s.label}
      />
      {s.agent ? (
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">{serviceNote(s)}</span>
      ) : (
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">
          This machine has no upgrade agent to start it (Lens wasn’t installed with install.sh, or it couldn’t set one
          up). On the machine, run <code className="break-all font-mono">{s.manual}</code>
        </span>
      )}
      {s.error && (
        <Banner tone="error" title="The machine couldn’t change it.">
          {s.error}
        </Banner>
      )}
      {turn.isError && <Banner tone="error">{turn.error.message}</Banner>}
    </div>
  );
}
