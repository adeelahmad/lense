"use client";

import { AudioLines, FileText, FolderOpen, Lock, Star, Video } from "lucide-react";
import Link from "next/link";

import type { PublicCard, PublicCollectionSummary } from "@/app/openapi-client/types.gen";
import { cardLine, collectionPath, publicPath } from "@/components/public/model";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

const KIND_ICON = { audio: AudioLines, video: Video, transcript: FileText } as const;

/**
 * A recording in a list of the pages visitors see. A locked one (restricted, seen signed in without permission) keeps
 * its title, with a lock in the corner and "Content locked" over its picture on hover, as Aviary shows it.
 */
export function RecordingCard({
  card,
  showNamespace,
  showFeatured = true,
}: {
  card: PublicCard;
  showNamespace?: boolean;
  /** Off where every card is featured (the home page's Featured section). */
  showFeatured?: boolean;
}) {
  const locked = card.view === "locked";
  const Icon = KIND_ICON[card.media_kind];
  return (
    <Link
      href={publicPath(card.id)}
      aria-label={locked ? `${card.title || "Untitled"} (content locked)` : undefined}
      className="group flex min-w-0 flex-col overflow-hidden rounded-md border border-border bg-background transition-shadow duration-fast hover:shadow-2 focus-visible:shadow-2"
    >
      <div className="relative aspect-[16/9] bg-surface-neutral">
        {card.poster ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={card.poster} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span
            className={cn(
              "grid size-full place-items-center text-fg-muted transition-opacity duration-fast",
              locked && "group-hover:opacity-0 group-focus-visible:opacity-0",
            )}
          >
            <Icon aria-hidden className="size-8" />
          </span>
        )}
        {locked && (
          <>
            <span className="absolute left-2 top-2 grid size-7 place-items-center rounded-full bg-background text-fg shadow-1">
              <Lock aria-hidden className="size-3.5" />
            </span>
            <span
              aria-hidden
              className="absolute inset-0 grid place-items-center bg-[var(--scrim)] text-[13.5px] font-bold text-fg-on-color opacity-0 transition-opacity duration-fast group-hover:opacity-100 group-focus-visible:opacity-100"
            >
              Content locked
            </span>
          </>
        )}
        {showFeatured && card.featured && !locked && (
          <span className="absolute left-2 top-2 flex h-6 items-center gap-1 rounded-pill bg-background px-2 text-[11.5px] font-semibold text-fg shadow-1">
            <Star aria-hidden className="size-3 fill-current" />
            Featured
          </span>
        )}
        {card.duration_ms ? (
          <span className="tabular absolute bottom-2 right-2 rounded-xs bg-background px-1.5 py-0.5 text-[11.5px] font-semibold text-fg shadow-1">
            {tc(card.duration_ms)}
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-1 p-3.5">
        {showNamespace && card.namespace && (
          <span className="truncate text-[12px] font-semibold text-fg-muted">{card.namespace}</span>
        )}
        <h3 className="line-clamp-2 text-[15px] font-bold leading-snug text-fg group-hover:underline">
          {card.title || "Untitled"}
        </h3>
        <span className="tabular text-[12.5px] text-fg-secondary">{cardLine(card)}</span>
        {card.summary && <p className="line-clamp-3 text-[13px] leading-[1.45] text-fg-secondary">{card.summary}</p>}
      </div>
    </Link>
  );
}

/** A collection on the home page: its name, what it's about and how many of its recordings this visitor sees. */
export function CollectionCard({ c }: { c: PublicCollectionSummary }) {
  return (
    <Link
      href={collectionPath(c.name)}
      className="group flex min-w-0 items-start gap-3 rounded-md border border-border bg-background p-3.5 transition-shadow duration-fast hover:shadow-2 focus-visible:shadow-2"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-surface-neutral text-fg-secondary">
        <FolderOpen aria-hidden className="size-[18px]" />
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-[14.5px] font-bold text-fg group-hover:underline">{c.label}</span>
        <span className={cn("text-[12.5px] text-fg-secondary", !c.summary && "text-fg-muted")}>
          {c.recordings} recording{c.recordings === 1 ? "" : "s"}
          {c.member ? " · you’re a member" : c.network ? ` · open from ${c.network}` : ""}
        </span>
        {c.summary && <span className="line-clamp-2 text-[13px] leading-[1.45] text-fg-secondary">{c.summary}</span>}
      </span>
    </Link>
  );
}
