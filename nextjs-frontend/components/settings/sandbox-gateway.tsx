"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Updates } from "@/app/openapi-client";
import type { SandboxGateway } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Switch } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";

/** One of the API's reasons as a sentence: capitalised, with a full stop. */
export function sentence(s: string): string {
  const t = s.trim();
  return t ? `${t.charAt(0).toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t) ? "" : "."}` : t;
}

/** What the switch says under it: waiting for the machine, starting, stopping, running (and reached) or off. */
export function gatewayNote(g: SandboxGateway): string {
  if (g.state === "queued") return "Waiting for the machine to pick it up…";
  if (g.state === "running")
    return g.on
      ? "Starting the gateway (the first start downloads its images, about 300 MB on disk)…"
      : "Deleting the sandboxes and stopping the gateway…";
  if (g.on && g.state === "done")
    return g.reachable === false ? "Running, but Lens can’t reach it yet." : `Running at ${g.address}.`;
  return "Off. On starts NVIDIA OpenShell’s gateway next to Lens and turns the sandbox on; off deletes every sandbox, files and all.";
}

/** Whether the code sandbox's gateway runs next to Lens, while the machine changes it. */
export function useSandboxGateway() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["sandbox-gateway"],
    queryFn: () => data(Updates.getSandboxGateway({ client })),
    refetchInterval: (q) => (q.state.data?.state === "queued" || q.state.data?.state === "running" ? 2000 : false),
  });
}

/** The code sandbox's OpenShell gateway on or off (the Compose profile `sandbox`), started by the upgrade agent. */
export function SandboxGatewaySwitch() {
  const client = useApiClient();
  const qc = useQueryClient();
  const q = useSandboxGateway();
  const turn = useMutation({
    mutationFn: (on: boolean) => data(Updates.setSandboxGateway({ client, body: { on } })),
    onSuccess: (g) => {
      qc.setQueryData(["sandbox-gateway"], g);
      void qc.invalidateQueries({ queryKey: ["settings"] }); // on turns the sandbox on, at this gateway; off, off
    },
  });
  const g = q.data;
  if (!g) return null;
  const busy = turn.isPending || ((g.state === "queued" || g.state === "running") && !g.error);
  const blocked = g.preflight.length > 0 && !g.on;
  return (
    <div className="flex flex-col gap-2">
      <Switch
        checked={turn.isPending ? Boolean(turn.variables) : g.on}
        onCheckedChange={(on) => turn.mutate(on)}
        disabled={busy || !g.agent || blocked}
        label="Run the gateway on this machine"
      />
      {blocked ? (
        <Banner tone="warning" title="This machine can’t run the gateway.">
          {g.preflight.map((p) => (
            <p key={p}>{sentence(p)}</p>
          ))}
        </Banner>
      ) : g.agent ? (
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">{gatewayNote(g)}</span>
      ) : (
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">
          This machine has no upgrade agent to start it (Lens wasn’t installed with install.sh, or it couldn’t set one
          up). On the machine, run <code className="break-all font-mono">{g.manual}</code>
        </span>
      )}
      {g.error && (
        <Banner tone="error" title="The machine couldn’t change it.">
          {g.error}
          {g.agent && (
            <div className="mt-1">
              By hand, on the machine: <code className="break-all font-mono">{g.manual}</code>
            </div>
          )}
        </Banner>
      )}
      {g.reachable === false && g.state !== "queued" && g.state !== "running" && (
        <Banner tone="warning" title={`Lens can’t reach the gateway at ${g.address}.`}>
          {g.problem}
        </Banner>
      )}
      {turn.isError && <Banner tone="error">{turn.error.message}</Banner>}
    </div>
  );
}
