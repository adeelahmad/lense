"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Speakers } from "@/app/openapi-client";
import type { Speaker } from "@/app/openapi-client/types.gen";
import type { ReviewPair } from "@/components/speakers/derive";
import { InlinePlayerBar, useInlinePlayer } from "@/components/search/player";
import { isUnnamed, similarityWord, talkTime } from "@/components/speakers/format";
import { ClipRow, SpeakerAvatar, useSpeakerClips } from "@/components/speakers/parts";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

function Side({ s, player }: { s: Speaker; player: ReturnType<typeof useInlinePlayer> }) {
  const { clips, isLoading } = useSpeakerClips(s.id, { recordings: 3, perRecording: 1, max: 3 });
  const meta = [isUnnamed(s) ? "unnamed" : null, plural(s.recordings ?? 0, "recording"), talkTime(s.talk_ms)].filter(Boolean).join(" · ");
  return (
    <section aria-label={s.display} className="flex flex-col gap-3 rounded-lg border border-border p-[18px]">
      <div className="flex items-center gap-2.5">
        <SpeakerAvatar s={s} size={36} />
        <span className="flex min-w-0 flex-col gap-[3px]">
          <b className="truncate text-[16px] font-bold leading-tight text-fg">{s.display}</b>
          <span className="text-[12.5px] text-fg-muted">{meta}</span>
        </span>
      </div>
      {isLoading && [0, 1, 2].map((i) => <Skeleton key={i} className="h-12 w-full rounded-[10px]" />)}
      {!isLoading && clips.length === 0 && <p className="m-0 text-[13px] text-fg-secondary">No lines to sample.</p>}
      {clips.map((c) => (
        <ClipRow key={c.key} clip={c} player={player} compact />
      ))}
    </section>
  );
}

/** SP2: one pair at a time — clips from both sides, the similarity in words and as a number, and the decision. */
export function ReviewQueue({ pairs, ns }: { pairs: ReviewPair[]; ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const player = useInlinePlayer();
  const [i, setI] = useState(0);
  const canMerge = can("editor", ns);
  const pair = pairs[Math.min(i, pairs.length - 1)];

  const undo = useMutation({
    mutationFn: (mid: number) => data(Speakers.undoSpeakerMerge({ client, path: { mid } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["speakers"] });
      toast({ title: "Merge undone", tone: "intent" });
    },
  });
  const merge = useMutation({
    mutationFn: (p: ReviewPair) => data(Speakers.mergeSpeaker({ client, path: { sid: p.a.id }, body: { into: p.b.id } })),
    onSuccess: (r, p) => {
      qc.invalidateQueries({ queryKey: ["speakers"] });
      qc.invalidateQueries({ queryKey: ["speaker-reviews"] });
      toast({ title: `Merged ${p.a.display} into ${p.b.display}`, tone: "green", action: { label: "Undo", onClick: () => undo.mutate(r.merge_id) } });
    },
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (!pair || e.metaKey || e.ctrlKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable) return;
      const k = e.key.toLowerCase();
      if (k === "s") setI((x) => (x + 1) % Math.max(1, pairs.length));
      else if (k === "m" && canMerge && !merge.isPending) merge.mutate(pair);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pair, pairs.length, canMerge, merge]);

  if (!pair)
    return (
      <EmptyState title="Nothing to review" className="rounded-lg border border-border">
        When a new voice sounds close to someone already in {ns} — but not close enough to match on its own — the pair waits here for a person to decide.
      </EmptyState>
    );
  const word = similarityWord(pair.score);
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border px-6 py-[22px]">
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="flex-1 text-[16px] font-bold text-fg" aria-live="polite">
          Review {Math.min(i, pairs.length - 1) + 1} of {pairs.length}
        </h2>
        <span className="text-[12.5px] text-fg-muted">
          <kbd className="font-sans">M</kbd> merge · <kbd className="font-sans">S</kbd> skip
        </span>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_200px_minmax(0,1fr)]">
        <Side s={pair.a} player={player} />
        <div className="order-first flex flex-col items-center justify-center gap-2.5 px-1.5 py-2 text-center lg:order-none">
          <span className="grid size-14 rotate-45 place-items-center rounded-[10px] border border-gold-border bg-gold-surface">
            <span className="tabular -rotate-45 text-[16px] font-extrabold text-fg">{pair.score.toFixed(2)}</span>
          </span>
          <b className="text-[16px] font-bold capitalize text-fg">{word}</b>
          <span className="text-[12.5px] leading-[1.45] text-fg-secondary">Close, but below the auto-match threshold (Settings → Voice IDs), so a person decides.</span>
        </div>
        <Side s={pair.b} player={player} />
      </div>
      <InlinePlayerBar player={player} />
      {merge.isError && <Banner tone="error">{merge.error.message}</Banner>}
      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <span className="min-w-[240px] flex-1 text-[13px] leading-[1.45] text-fg-secondary">
          Merging moves {pair.a.display}’s {plural(pair.a.recordings ?? 0, "recording")} and voice samples into {pair.b.display}. You can undo it from Merge history.
        </span>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => setI((x) => (x + 1) % pairs.length)} disabled={pairs.length < 2} disabledReason="This is the only pair">
            Skip
          </Button>
          <Button variant="secondary" disabled disabledReason="Not available yet: the archive can’t record “not the same” for speakers">
            Not the same
          </Button>
          <Button variant="approve" disabled={!canMerge || merge.isPending} disabledReason={canMerge ? undefined : needRole("editor", ns)} onClick={() => merge.mutate(pair)} className={cn(merge.isPending && "opacity-70")}>
            {merge.isPending ? "Merging…" : `Merge into ${pair.b.display}`}
          </Button>
        </div>
      </div>
    </div>
  );
}
