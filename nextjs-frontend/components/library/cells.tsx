"use client";

import { FileAudio, FileText, FileVideo } from "lucide-react";

import { Badge, EmotionBar, speakerColor } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { emotionMix, importanceInfo, speakerList, toneWord, type StatusView } from "@/components/library/model";
import { cn } from "@/lib/utils";

const SUB_TONE = {
  muted: "text-fg-muted",
  red: "text-red-dark",
  gold: "text-gold-dark",
};

/** Audio, video or transcript-only: the small icon before the namespace. */
export function MediaIcon({ kind, className }: { kind: string | null | undefined; className?: string }) {
  const Icon = kind === "video" ? FileVideo : kind === "audio" ? FileAudio : FileText;
  const label = kind === "video" ? "Video" : kind === "audio" ? "Audio" : "Transcript only";
  return <Icon aria-label={label} role="img" className={cn("size-3 shrink-0", className)} />;
}

/** "Text" marks transcript-only recordings (no audio). */
export function TextBadge() {
  return (
    <Tooltip content="Transcript only — no audio">
      <span
        tabIndex={0}
        className="inline-flex h-[18px] shrink-0 items-center gap-[3px] rounded-xs bg-surface-neutral px-1.5 text-[10.5px] font-semibold text-fg-secondary"
      >
        <FileText className="size-[11px]" aria-hidden />
        Text
      </span>
    </Tooltip>
  );
}

/** Status chip with the job overlay under it: the running step and a thin progress bar, a wait, or what failed. */
export function StatusCell({
  view,
  onRetry,
  retryDisabledReason,
  compact,
}: {
  view: StatusView;
  onRetry?: () => void;
  retryDisabledReason?: string;
  compact?: boolean;
}) {
  return (
    <span className="flex min-w-0 flex-col items-start gap-1">
      <Badge tone={view.tone} dot>
        {view.label}
      </Badge>
      {!compact && view.progress != null && (
        <span className="flex w-full items-center gap-1.5">
          <span className="h-[3px] min-w-[24px] flex-1 overflow-hidden rounded-pill bg-surface-neutral">
            <span
              className="block h-full bg-blue transition-[width] duration-slow"
              style={{ width: `${Math.round(view.progress * 100)}%` }}
            />
          </span>
          <span className="whitespace-nowrap text-[11px] font-medium text-fg-muted">{view.sub?.text}</span>
        </span>
      )}
      {!compact && view.progress == null && view.sub && (
        <span
          className={cn(
            "flex max-w-full items-center gap-1 whitespace-nowrap text-[11px] font-medium",
            SUB_TONE[view.sub.tone],
          )}
        >
          {view.sub.title ? (
            <Tooltip content={view.sub.title}>
              <span tabIndex={0} className="truncate">
                {view.sub.text}
              </span>
            </Tooltip>
          ) : (
            <span className="truncate">{view.sub.text}</span>
          )}
          {view.retry && onRetry && (
            <>
              <span aria-hidden>·</span>
              {retryDisabledReason ? (
                <Tooltip content={retryDisabledReason}>
                  <span tabIndex={0} aria-disabled className="cursor-not-allowed opacity-60">
                    Retry
                  </span>
                </Tooltip>
              ) : (
                <button type="button" onClick={onRetry} className="font-semibold hover:underline">
                  Retry
                </button>
              )}
            </>
          )}
        </span>
      )}
    </span>
  );
}

/** Overlapping speaker discs and the names. Unnamed speakers get a dashed ring. */
export function SpeakersCell({ speakers }: { speakers: string | null | undefined }) {
  const list = speakerList(speakers);
  if (!list.length) return <span className="text-fg-muted">—</span>;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="flex shrink-0" aria-hidden>
        {list.slice(0, 4).map((s) => (
          <span
            key={s.name}
            className="-mr-[5px] box-border size-[18px] rounded-full shadow-[0_0_0_2px_var(--background)]"
            style={
              s.unnamed
                ? {
                    border: "1.5px dashed var(--text-muted)",
                    background: "var(--background)",
                  }
                : { background: speakerColor(s.index) }
            }
          />
        ))}
      </span>
      <span
        className="truncate pl-1 text-[12px] font-semibold text-fg-strong"
        title={list.map((s) => s.name).join(", ")}
      >
        {list.map((s) => s.name).join(", ")}
      </span>
    </span>
  );
}

/** The emotion mix as a mini-bar, with the breakdown in a tooltip and for screen readers. */
export function EmotionCell({ emotions }: { emotions: Record<string, unknown> | null | undefined }) {
  const mix = emotionMix(emotions);
  const total = Object.values(mix).reduce((a, b) => a + b, 0);
  const tip = total
    ? Object.entries(mix)
        .sort((a, b) => b[1] - a[1])
        .map(([k, n]) => `${k} ${Math.round((n / total) * 100)}%`)
        .join(" · ")
    : "Not analysed yet";
  return (
    <Tooltip content={tip}>
      <span tabIndex={-1} className="block">
        <EmotionBar counts={mix} className="h-2 w-full gap-px bg-surface-neutral" />
      </span>
    </Tooltip>
  );
}

/** Three rising bars (Low · Medium · High) and the overall tone from the summary. */
export function ImportanceCell({ importance, sentiment }: { importance: unknown; sentiment: unknown }) {
  const imp = importanceInfo(importance);
  const tone = toneWord(sentiment);
  if (!imp && !tone) return <span className="text-fg-muted">—</span>;
  return (
    <span className="flex items-center gap-1.5 overflow-hidden whitespace-nowrap text-[12.5px] font-medium text-fg-secondary">
      {imp && (
        <>
          <span className="flex h-3 items-end gap-0.5" aria-hidden>
            {[1, 2, 3].map((i) => (
              <span
                key={i}
                className={cn("w-[3px] rounded-[1px]", i <= imp.bars ? "bg-fg-strong" : "bg-border")}
                style={{ height: i * 4 }}
              />
            ))}
          </span>
          <span className="text-fg-strong">
            <span className="sr-only">Importance </span>
            {imp.label}
          </span>
        </>
      )}
      {tone && <span className="truncate">{imp ? `· ${tone}` : tone}</span>}
    </span>
  );
}

/** A recording's tags as small chips; the rest as a count, all of them in the title. */
export function TagsCell({ tags }: { tags: string[] | null | undefined }) {
  const list = tags ?? [];
  if (!list.length) return <span className="text-fg-muted">—</span>;
  return (
    <span className="flex min-w-0 items-center gap-1 overflow-hidden" title={list.join(", ")}>
      {list.slice(0, 2).map((t) => (
        <span
          key={t}
          className="max-w-[96px] shrink-0 truncate rounded-pill border border-border bg-surface-neutral px-2 py-px text-[11.5px] font-medium text-fg-secondary"
        >
          {t}
        </span>
      ))}
      {list.length > 2 && <span className="shrink-0 text-[11.5px] text-fg-muted">+{list.length - 2}</span>}
    </span>
  );
}
