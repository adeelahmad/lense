"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { Resources } from "@/app/openapi-client";
import type { NsfwState } from "@/app/openapi-client/types.gen";
import { nsfwText } from "@/components/nsfw/nsfw-model";
import { useRec } from "@/components/recording/context";
import { rk, type RecordingDetail } from "@/components/recording/hooks";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Pictures = { blurred: boolean; reveal: () => void; cover: () => void };
const PicturesCtx = createContext<Pictures>({ blurred: false, reveal: () => {}, cover: () => {} });

/**
 * A resource's pictures (docs/nsfw.md): blurred while it's flagged NSFW in a namespace that blurs or hides what's
 * flagged, until someone chooses to see them.
 */
export function NsfwPictures({ state, children }: { state: NsfwState | null | undefined; children: ReactNode }) {
  const [shown, setShown] = useState(false);
  const blur = Boolean(state?.blur);
  const value = useMemo(
    () => ({ blurred: blur && !shown, reveal: () => setShown(true), cover: () => setShown(false) }),
    [blur, shown],
  );
  return <PicturesCtx.Provider value={value}>{children}</PicturesCtx.Provider>;
}

export const useNsfwPictures = () => useContext(PicturesCtx);

/** The class a picture takes while its resource's pictures are blurred ("" otherwise). */
export function useBlur(): string {
  return useContext(PicturesCtx).blurred ? "blur-xl" : "";
}

/** Over a large picture (the video, a document's page) while it's blurred: the button that shows it. */
export function RevealCover({ className }: { className?: string }) {
  const p = useNsfwPictures();
  if (!p.blurred) return null;
  return (
    <div className={cn("absolute inset-0 z-10 grid place-items-center", className)}>
      <Button variant="secondary" size="sm" icon={<Eye />} onClick={p.reveal}>
        Show NSFW pictures
      </Button>
    </div>
  );
}

/** Mark the resource NSFW or not by hand (editors), or hand it back to the model; each with Undo. */
export function useMarkNsfw() {
  const { id, rec } = useRec();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const mark = useMutation({
    mutationFn: (flagged: boolean | null) =>
      data(Resources.markRecordingNsfw({ client, path: { rid: id }, body: { flagged } })),
    onMutate: () => ({ before: rec.nsfw?.manual ?? null }),
    onSuccess: (got, flagged, ctx) => {
      qc.setQueryData(rk.detail(id), (old: RecordingDetail | undefined) =>
        old ? { ...old, nsfw: got ? { ...got, hits: old.nsfw?.hits } : null } : old,
      );
      void qc.invalidateQueries({ queryKey: rk.player(id) });
      void qc.invalidateQueries({ queryKey: ["recordings"] });
      toast({
        title: flagged === null ? "The model’s score decides again" : flagged ? "Marked NSFW" : "Marked not NSFW",
        action: { label: "Undo", onClick: () => mark.mutate(ctx?.before ?? null) },
      });
    },
    onError: (e) => toast({ title: "Couldn’t mark it", body: (e as Error).message, tone: "red" }),
  });
  return mark;
}

/** The buttons that mark a resource: Not NSFW or Mark NSFW, and Let the model decide once a person has. */
export function NsfwMarkButtons({ state }: { state: NsfwState }) {
  const { canEdit, ns } = useRec();
  const mark = useMarkNsfw();
  const why = !canEdit ? needRole("editor", ns) : undefined;
  return (
    <span className="inline-flex flex-wrap gap-1.5">
      <Button
        variant="ghost"
        size="xs"
        disabled={!canEdit || mark.isPending}
        disabledReason={why}
        onClick={() => mark.mutate(!state.flagged)}
      >
        {state.flagged ? "Not NSFW" : "Mark NSFW"}
      </Button>
      {state.manual != null && state.score != null && (
        <Button
          variant="ghost"
          size="xs"
          disabled={!canEdit || mark.isPending}
          disabledReason={why}
          onClick={() => mark.mutate(null)}
        >
          Let the model decide
        </Button>
      )}
    </span>
  );
}

/** The recording page's notice for a resource flagged NSFW: what was found, show or blur its pictures, mark it. */
export function NsfwNotice({ className }: { className?: string }) {
  const { rec, paged } = useRec();
  const p = useNsfwPictures();
  const n = rec.nsfw;
  if (!n?.flagged) return null;
  return (
    <div
      role="status"
      className={cn(
        "mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-gold-border bg-gold-surface py-2 pl-3.5 pr-3",
        className,
      )}
    >
      <EyeOff aria-hidden className="size-4 shrink-0 text-gold-dark" />
      <p className="min-w-[220px] flex-1 text-[13px] leading-[1.45] text-fg-strong">
        <b className="text-fg">{nsfwText(n, paged)}.</b>
        {n.blur && p.blurred ? " Its pictures are blurred." : ""}
      </p>
      {n.blur && (
        <Button
          variant="ghost"
          size="xs"
          icon={p.blurred ? <Eye /> : <EyeOff />}
          onClick={p.blurred ? p.reveal : p.cover}
        >
          {p.blurred ? "Show pictures" : "Blur them"}
        </Button>
      )}
      <NsfwMarkButtons state={n} />
    </div>
  );
}

/** The Details tab's line: what classification found, and the buttons that mark it. */
export function NsfwDetail() {
  const { rec, paged } = useRec();
  const n = rec.nsfw;
  if (!n) return null;
  return (
    <span className="flex flex-col items-start gap-1">
      <span>{nsfwText(n, paged)}</span>
      <NsfwMarkButtons state={n} />
    </span>
  );
}
