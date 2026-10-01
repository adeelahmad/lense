"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link2, Unlink } from "lucide-react";

import { Speakers } from "@/app/openapi-client";
import type { SpeakerCandidate, SpeakerLink, SpeakerMerge } from "@/app/openapi-client/types.gen";
import { mergeDetail } from "@/components/speakers/format";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
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
                {mergeDetail(m, shortDate(m.at, true), m.undone_at ? shortDate(m.undone_at, true) : null)}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** SP3 (right): speakers linked to people in other namespaces, and voices the archive thinks may be the same. */
export function OtherNamespaces({ links, cross }: { links: SpeakerLink[]; cross: SpeakerCandidate[] }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const done = () => {
    qc.invalidateQueries({ queryKey: ["speakers"] });
    qc.invalidateQueries({ queryKey: ["graph"] });
  };
  const fail = (title: string) => (e: Error) => toast({ title, body: e.message, tone: "red" });
  const link = useMutation({
    mutationFn: (s: SpeakerCandidate) =>
      data(Speakers.linkSpeaker({ client, path: { sid: s.a }, body: { with: s.b } })),
    onSuccess: (_r, s) => {
      done();
      toast({
        title: `Linked ${s.a_name} and ${s.b_name}`,
        body: "They show as the same person in the graph and profiles.",
        tone: "green",
      });
    },
    onError: fail("Couldn’t link them"),
  });
  const dismiss = useMutation({
    mutationFn: (s: SpeakerCandidate) =>
      data(Speakers.notSameSpeaker({ client, path: { sid: s.a }, body: { with: s.b } })),
    onSuccess: (_r, s) => {
      done();
      toast({ title: `${s.a_name} and ${s.b_name} aren’t the same person`, body: "This won’t be suggested again." });
    },
    onError: fail("Couldn’t dismiss it"),
  });
  const unlink = useMutation({
    mutationFn: (l: SpeakerLink) => data(Speakers.unlinkSpeaker({ client, path: { sid: l.a }, body: { with: l.b } })),
    onSuccess: (_r, l) => {
      done();
      toast({ title: `Unlinked ${l.a_name} and ${l.b_name}`, body: "They show as different people again." });
    },
    onError: fail("Couldn’t unlink them"),
  });
  const both = (a: string | null | undefined, b: string | null | undefined) =>
    can("editor", a ?? undefined) && can("editor", b ?? undefined);
  const why = (a: string | null | undefined, b: string | null | undefined, what: string) =>
    `${needRole("editor", can("editor", a ?? undefined) ? (b ?? undefined) : (a ?? undefined))} (${what} needs editor access to both)`;
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
      {cross.length === 0 && links.length === 0 && (
        <p className="m-0 text-[13px] text-fg-secondary">No suggestions right now.</p>
      )}
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
        {cross.map((s) => {
          const allowed = both(s.a_ns, s.b_ns);
          return (
            <li
              key={`${s.a}-${s.b}`}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 rounded-md border border-border p-3"
            >
              <span className="flex flex-col gap-1">
                <span className="text-[13.5px] font-semibold leading-snug text-fg">
                  {s.a_name} · {s.a_ns} <span className="text-fg-muted">↔</span> {s.b_name} · {s.b_ns}
                </span>
                <span className="tabular text-[12px] text-fg-muted">
                  Voices {s.score.toFixed(2)} alike · above the auto-match threshold
                </span>
              </span>
              <span className="flex gap-1.5">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!allowed || dismiss.isPending}
                  disabledReason={allowed ? undefined : why(s.a_ns, s.b_ns, "saying they’re different")}
                  onClick={() => dismiss.mutate(s)}
                >
                  Not the same
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Link2 />}
                  disabled={!allowed || link.isPending}
                  disabledReason={allowed ? undefined : why(s.a_ns, s.b_ns, "linking")}
                  onClick={() => link.mutate(s)}
                >
                  Link
                </Button>
              </span>
            </li>
          );
        })}
        {links.map((l) => {
          const allowed = both(l.a_ns, l.b_ns);
          return (
            <li
              key={`${l.a}-${l.b}`}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 rounded-md border border-border bg-surface p-3"
            >
              <span className="flex flex-col gap-1">
                <span className="text-[13.5px] font-semibold leading-snug text-fg">
                  {l.a_name} · {l.a_ns} <span className="text-fg-muted">↔</span> {l.b_name} · {l.b_ns}
                </span>
                <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-green-dark">
                  <Link2 className="size-3.5" aria-hidden /> Linked
                </span>
              </span>
              <Button
                size="sm"
                variant="ghost"
                icon={<Unlink />}
                disabled={!allowed || unlink.isPending}
                disabledReason={allowed ? undefined : why(l.a_ns, l.b_ns, "unlinking")}
                onClick={() => unlink.mutate(l)}
              >
                Unlink
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
