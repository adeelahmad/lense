"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";

import { Search, Speakers } from "@/app/openapi-client";
import type { SpeakerLink, SpeakerMerge } from "@/app/openapi-client/types.gen";
import type { GraphData } from "@/components/graph/model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute, shortDate } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

/** SP3 (left): merges in this namespace, newest first; each can be undone once. */
export function MergeHistory({ merges, ns }: { merges: SpeakerMerge[]; ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const canUndo = can("editor", ns);
  const undo = useMutation({
    mutationFn: (mid: number) => data(Speakers.undoSpeakerMerge({ client, path: { mid } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["speakers"] });
      toast({
        title: "Merge undone",
        body: "The speaker and their recordings are back as they were.",
        tone: "green",
      });
    },
  });
  return (
    <section
      aria-labelledby="merge-history"
      className="flex max-w-[760px] flex-col gap-2.5 rounded-lg border border-border px-[22px] py-5"
    >
      <h2 id="merge-history" className="text-[16px] font-bold text-fg">
        Merge history
      </h2>
      {undo.isError && <Banner tone="error">{undo.error.message}</Banner>}
      {merges.length === 0 && (
        <p className="m-0 border-t border-border pt-3 text-[13px] text-fg-secondary">
          No merges in {ns} yet. Merges made from the review queue show here and can be undone.
        </p>
      )}
      <ul className="m-0 flex list-none flex-col p-0">
        {merges.map((m) => {
          const undone = Boolean(m.undone);
          return (
            <li
              key={m.id}
              className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-t border-border py-2.5"
            >
              <span className="text-[13.5px] font-semibold leading-snug text-fg">
                {m.from_name || m.from_label || `Speaker #${m.from_id}`} → {m.into_name ?? `Speaker #${m.into_id}`}
              </span>
              {undone ? (
                <span className="text-[12.5px] font-semibold text-fg-muted">Undone</span>
              ) : (
                <Button
                  size="xs"
                  variant="link"
                  className="h-auto"
                  disabled={!canUndo || undo.isPending}
                  disabledReason={canUndo ? undefined : needRole("editor", ns)}
                  onClick={() => undo.mutate(m.id)}
                >
                  Undo
                </Button>
              )}
              <span className="text-[12px] text-fg-muted" title={absolute(m.at)}>
                {shortDate(m.at, true)}
                {undone ? " · undone" : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

type Suggestion = {
  a: { id: number; name: string; ns: string };
  b: { id: number; name: string; ns: string };
};

/** Likely same voice in another shared namespace: graph edges the archive suggests (never merged). */
export function useVoiceSuggestions(ns: string) {
  const client = useApiClient();
  const g = useQuery({
    queryKey: ["graph", "global"],
    queryFn: () => data(Search.getGraph({ client, query: { scope: "global" } })) as Promise<GraphData>,
    staleTime: 60_000,
  });
  const nodes = new Map((g.data?.nodes ?? []).map((n) => [n.id, n]));
  const out: Suggestion[] = [];
  for (const e of g.data?.edges ?? []) {
    if (e.kind !== "maybe the same voice") continue;
    const x = nodes.get(e.a);
    const y = nodes.get(e.b);
    if (!x || !y) continue;
    const [mine, other] = x.ns.includes(ns) ? [x, y] : y.ns.includes(ns) ? [y, x] : [null, null];
    if (!mine || !other) continue;
    out.push({
      a: { id: mine.refs[0], name: mine.label, ns },
      b: { id: other.refs[0], name: other.label, ns: other.ns[0] },
    });
  }
  return { suggestions: out, isLoading: g.isLoading, isError: g.isError };
}

/** SP3 (right): speakers linked to people in other namespaces, and voices the archive thinks may be the same. */
export function OtherNamespaces({ links, ns }: { links: SpeakerLink[]; ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const { suggestions, isLoading } = useVoiceSuggestions(ns);
  const linked = new Set(links.map((l) => [l.a, l.b].sort().join("-")));
  const open = suggestions.filter((s) => !linked.has([s.a.id, s.b.id].sort().join("-")));
  const link = useMutation({
    mutationFn: (s: Suggestion) =>
      data(
        Speakers.linkSpeaker({
          client,
          path: { sid: s.a.id },
          body: { with: s.b.id },
        }),
      ),
    onSuccess: (_r, s) => {
      qc.invalidateQueries({ queryKey: ["speakers"] });
      qc.invalidateQueries({ queryKey: ["graph"] });
      toast({
        title: `Linked ${s.a.name} and ${s.b.name}`,
        body: "They show as the same person in the graph and profiles.",
        tone: "green",
      });
    },
  });
  return (
    <section
      aria-labelledby="other-ns"
      className="flex max-w-[760px] flex-col gap-2.5 rounded-lg border border-border px-[22px] py-5"
    >
      <h2 id="other-ns" className="text-[16px] font-bold text-fg">
        Maybe the same voice · other namespaces
      </h2>
      <p className="m-0 text-[12.5px] leading-[1.45] text-fg-secondary">
        Only between shared namespaces you belong to. Linking shows both identities as the same person in the graph and
        profiles; each keeps its own recordings and voiceprint.
      </p>
      {link.isError && <Banner tone="error">{link.error.message}</Banner>}
      {isLoading && <Skeleton className="h-14 w-full rounded-md" />}
      {!isLoading && open.length === 0 && links.length === 0 && (
        <p className="m-0 text-[13px] text-fg-secondary">No suggestions right now.</p>
      )}
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
        {open.map((s) => {
          const allowed = can("editor", s.a.ns) && can("editor", s.b.ns);
          return (
            <li
              key={`${s.a.id}-${s.b.id}`}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 rounded-md border border-border p-3"
            >
              <span className="flex flex-col gap-1">
                <span className="text-[13.5px] font-semibold leading-snug text-fg">
                  {s.a.name} · {s.a.ns} <span className="text-fg-muted">↔</span> {s.b.name} · {s.b.ns}
                </span>
                <span className="text-[12px] text-fg-muted">Voices match above the auto-match threshold</span>
              </span>
              <span className="flex gap-1.5">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled
                  disabledReason="Not available yet: suggestions can’t be dismissed"
                >
                  Dismiss
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Link2 />}
                  disabled={!allowed || link.isPending}
                  disabledReason={
                    allowed
                      ? undefined
                      : `${needRole("editor", can("editor", s.a.ns) ? s.b.ns : s.a.ns)} (linking needs editor access to both)`
                  }
                  onClick={() => link.mutate(s)}
                >
                  Link
                </Button>
              </span>
            </li>
          );
        })}
        {links.map((l) => (
          <li
            key={`${l.a}-${l.b}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 rounded-md border border-border bg-surface p-3"
          >
            <span className="text-[13.5px] font-semibold leading-snug text-fg">
              {l.a_name} · {l.a_ns} <span className="text-fg-muted">↔</span> {l.b_name} · {l.b_ns}
            </span>
            <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-green-dark">
              <Link2 className="size-3.5" aria-hidden /> Linked
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
