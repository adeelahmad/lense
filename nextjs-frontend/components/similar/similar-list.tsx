"use client";

import { ArrowRight, Hash, Sparkle, Tag } from "lucide-react";
import Link from "next/link";

import type { SimilarItem, SimilarResults } from "@/app/openapi-client/types.gen";
import { MediaIcon } from "@/components/library/cells";
import { Snippet } from "@/components/search/results";
import { MATCH_LABEL, citeHref, similarHref, useSimilar, type SimilarBasis } from "@/components/similar/model";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { shortDate, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Why a result is alike: close in meaning (with how close), shared key words, topics, entities. */
function Why({ item }: { item: SimilarItem }) {
  const words = item.words ?? [];
  const topics = item.topics ?? [];
  const entities = item.entities ?? [];
  return (
    <span className="flex flex-wrap items-center gap-1">
      {item.match.map((m) => (
        <Tooltip
          key={m}
          content={
            m === "meaning"
              ? `Close in meaning${item.similarity != null ? ` (${Math.round(item.similarity * 100)}% alike)` : ""}: about the same thing, in other words`
              : m === "words"
                ? `Says ${words.slice(0, 6).join(", ") || "the same key words"}`
                : m === "topics"
                  ? `About ${topics.map((t) => t.label).join(", ")}`
                  : `Mentions ${entities.map((e) => e.name).join(", ")}`
          }
        >
          <span
            tabIndex={0}
            className={cn(
              "inline-flex h-[18px] items-center gap-1 rounded-pill px-1.5 text-[10.5px] font-bold uppercase tracking-[.04em]",
              m === "meaning" ? "bg-blue-surface text-blue-dark" : "bg-surface-neutral text-fg-secondary",
            )}
          >
            {m === "meaning" && <Sparkle aria-hidden className="size-2.5" />}
            {MATCH_LABEL[m] ?? m}
          </span>
        </Tooltip>
      ))}
    </span>
  );
}

/** One alike resource: its title, where it is, why it's alike, and the moments that show it (each opens there). */
export function SimilarRow({ item, compact, onOpen }: { item: SimilarItem; compact?: boolean; onOpen?: () => void }) {
  const all = item.cites ?? [];
  const cites = compact ? all.slice(0, 1) : all;
  const topics = item.topics ?? [];
  const entities = item.entities ?? [];
  const meta = [item.namespace, item.recorded_at ? shortDate(item.recorded_at) : null].filter(Boolean).join(" · ");
  return (
    <li className={cn("flex flex-col gap-1.5", compact ? "py-2" : "py-3")}>
      <div className="flex min-w-0 items-baseline gap-2">
        <MediaIcon kind={item.media_kind} className="translate-y-[1px] text-fg-muted" />
        <Link
          href={`/resources/${item.recording_id}`}
          onClick={onOpen}
          className={cn(
            "min-w-0 flex-1 font-semibold text-fg hover:text-fg-accent hover:underline",
            compact ? "truncate text-[13.5px]" : "text-[14.5px]",
          )}
        >
          {item.title || `Resource ${item.recording_id}`}
        </Link>
        {meta && !compact && <span className="shrink-0 text-[12px] text-fg-muted">{meta}</span>}
      </div>
      {!compact && <Why item={item} />}
      {cites.length > 0 && (
        <ul className="flex flex-col gap-1">
          {cites.map((c, i) => (
            <li key={i}>
              <Link
                href={citeHref(item.recording_id, c)}
                onClick={onOpen}
                className="group flex items-baseline gap-2 rounded-sm text-left hover:bg-hl"
              >
                <span className="tabular w-[44px] shrink-0 text-[11.5px] font-semibold text-fg-secondary group-hover:text-fg-accent">
                  {c.page != null ? `p. ${c.page + 1}` : c.t0 != null ? tc(c.t0) : ""}
                </span>
                <Snippet
                  html={c.snippet}
                  className={cn(
                    "min-w-0 flex-1 font-serif leading-[1.45] text-fg [text-wrap:pretty]",
                    compact ? "line-clamp-2 text-[13px]" : "line-clamp-3 text-[14px]",
                  )}
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
      {!compact && (topics.length > 0 || entities.length > 0) && (
        <div className="flex flex-wrap gap-1">
          {topics.slice(0, 5).map((t) => (
            <Link
              key={`t${t.id}`}
              href={`/topics/${t.id}`}
              className="inline-flex h-[20px] items-center gap-1 rounded-xs bg-surface-neutral px-1.5 text-[11.5px] font-medium text-fg-secondary hover:text-fg"
            >
              <Hash aria-hidden className="size-3" />
              {t.label}
            </Link>
          ))}
          {entities.slice(0, 5).map((e) => (
            <Link
              key={`e${e.id}`}
              href={`/entities/${e.id}`}
              className="inline-flex h-[20px] items-center gap-1 rounded-xs bg-surface-neutral px-1.5 text-[11.5px] font-medium text-fg-secondary hover:text-fg"
            >
              <Tag aria-hidden className="size-3" />
              {e.name}
            </Link>
          ))}
        </div>
      )}
    </li>
  );
}

export function SimilarRows({
  items,
  compact,
  onOpen,
}: {
  items: SimilarItem[];
  compact?: boolean;
  onOpen?: () => void;
}) {
  return (
    <ol className={cn("flex flex-col divide-y divide-border", compact && "max-h-[340px] overflow-y-auto")}>
      {items.map((x) => (
        <SimilarRow key={x.recording_id} item={x} compact={compact} onOpen={onOpen} />
      ))}
    </ol>
  );
}

/** How the results were found, in a line: by meaning too, or by words, topics and entities. */
export function SimilarNote({ res, className }: { res: SimilarResults; className?: string }) {
  const how =
    res.mode === "meaning"
      ? "By meaning, key words, topics and entities."
      : "By key words, topics and entities." +
        (res.meaning ? ` ${res.meaning[0].toUpperCase()}${res.meaning.slice(1)}.` : "");
  return <p className={cn("text-[12px] leading-snug text-fg-muted", className)}>{how}</p>;
}

/** Content like `basis`: loading, nothing found, or the first `limit` with a link to all of them. */
export function SimilarResultsView({
  basis,
  limit = 10,
  compact,
  enabled = true,
  onOpen,
  empty,
}: {
  basis: SimilarBasis | null;
  limit?: number;
  compact?: boolean;
  enabled?: boolean;
  onOpen?: () => void;
  empty?: string;
}) {
  const q = useSimilar(basis, { limit, enabled });
  if (!basis) return null;
  if (q.isLoading)
    return (
      <div className="flex flex-col gap-3 py-2" aria-busy="true" aria-label="Finding similar content">
        {Array.from({ length: compact ? 3 : 4 }, (_, i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <Skeleton className="w-[55%]" />
            <Skeleton className="w-[90%]" />
          </div>
        ))}
      </div>
    );
  if (q.isError)
    return (
      <p role="alert" className="py-2 text-[13px] text-red-dark">
        Couldn&apos;t find similar content: {q.error instanceof Error ? q.error.message : "try again"}.
      </p>
    );
  const res = q.data;
  if (!res?.items.length)
    return compact ? (
      <p className="py-2 text-[13px] text-fg-secondary">{empty ?? "Nothing similar found."}</p>
    ) : (
      <EmptyState title="Nothing similar yet" className="py-10">
        {empty ??
          "Nothing else you can read shares its meaning, key words, topics or entities. Similar content shows up as more is added and analysed."}
      </EmptyState>
    );
  return (
    <div className="flex flex-col gap-1">
      <SimilarRows items={res.items} compact={compact} onOpen={onOpen} />
      <div className="flex items-center gap-3 pt-1">
        <SimilarNote res={res} className="flex-1" />
        <Link
          href={similarHref(basis)}
          onClick={onOpen}
          className="inline-flex shrink-0 items-center gap-1 text-[13px] font-semibold text-fg-accent hover:underline"
        >
          Find more{res.total > res.items.length ? ` (${res.total})` : ""}
          <ArrowRight aria-hidden className="size-3.5" />
        </Link>
      </div>
    </div>
  );
}
