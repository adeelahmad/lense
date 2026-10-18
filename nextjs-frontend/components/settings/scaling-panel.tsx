"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Scaling } from "@/app/openapi-client";
import type { Group, Provider } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/** One of the API's reasons as a sentence: capitalised, with a full stop. */
function sentence(s: string): string {
  const t = s.trim();
  return t ? `${t.charAt(0).toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t) ? "" : "."}` : t;
}

/** What approving a group binds, in words: how many units at most, where, and what they may cost. */
export function termsText(g: Group, providers: Provider[]): string {
  const p = providers.find((x) => x.id === g.provider);
  const where = p ? ` on ${p.name}` : "";
  const usd = g.budget?.usd;
  const cost = usd != null ? `, up to $${usd} a month` : p && !p.priced ? ", at no cost" : "";
  const cap = g.at_cap === "min" && usd != null ? ` (at the cap, ${g.min} keep running)` : "";
  return `Up to ${g.max} unit${g.max === 1 ? "" : "s"}${where}${cost}${cap}`;
}

/** Where a group stands: its units now, and why it launches nothing when it doesn't. */
export function groupState(g: Group): string {
  const u = g.units;
  const parts = [`${u.running} of ${g.max} running`];
  if (u.launching) parts.push(`${u.launching} joining`);
  if (u.draining) parts.push(`${u.draining} leaving`);
  return parts.join(", ") + (g.waiting_for ? `. Waiting for ${g.waiting_for}` : "");
}

/** The scaling groups: each one's terms and units, approving its terms (a person's pick, here), pausing it. */
export function ScalingGroups() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const groups = useQuery({
    queryKey: ["scaling-groups"],
    queryFn: () => data(Scaling.listGroups({ client })),
    refetchInterval: 15_000,
  });
  const providers = useQuery({
    queryKey: ["scaling-providers"],
    queryFn: () => data(Scaling.listProviders({ client })),
  });
  const fail = (title: string) => (e: Error) => toast({ title, body: e.message, tone: "red" });
  const done = () => void qc.invalidateQueries({ queryKey: ["scaling-groups"] });
  const approve = useMutation({
    mutationFn: (gid: number) => data(Scaling.approveGroup({ client, path: { gid } })),
    onSuccess: (g) => {
      done();
      toast({ title: `Approved ${g.name}`, tone: "green" });
    },
    onError: fail("Couldn’t approve the group"),
  });
  const pause = useMutation({
    mutationFn: ({ gid, paused }: { gid: number; paused: boolean }) =>
      data(Scaling.pauseGroup({ client, path: { gid }, body: { paused } })),
    onSuccess: done,
    onError: fail("Couldn’t change the group"),
  });
  if (groups.isPending) return <SkeletonRows rows={2} />;
  if (groups.error)
    return (
      <Banner tone="error" title="Couldn’t read the scaling groups.">
        {groups.error.message}
      </Banner>
    );
  const list = groups.data;
  if (!list.length)
    return (
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
        No scaling groups yet. Ask the assistant to set one up, or make one over the API or MCP; it shows here for you
        to approve.
      </p>
    );
  return (
    <ul aria-label="Scaling groups" className="flex flex-col divide-y divide-border rounded-md border border-border">
      {list.map((g) => (
        <li key={g.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 text-[13px]">
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-fg">{g.name}</div>
            <div className="text-fg-secondary">{termsText(g, providers.data ?? [])}</div>
            <div className="text-[12.5px] text-fg-secondary">{sentence(groupState(g))}</div>
          </div>
          <div className="flex gap-2">
            {!g.approved && (
              <Button
                size="xs"
                variant="approve"
                aria-label={`Approve ${g.name}: ${termsText(g, providers.data ?? [])}`}
                disabled={approve.isPending}
                onClick={() => approve.mutate(g.id)}
              >
                Approve
              </Button>
            )}
            <Button
              size="xs"
              variant="ghost"
              disabled={pause.isPending}
              onClick={() => pause.mutate({ gid: g.id, paused: !g.paused })}
            >
              {g.paused ? "Resume" : "Pause"}
            </Button>
          </div>
        </li>
      ))}
    </ul>
  );
}
