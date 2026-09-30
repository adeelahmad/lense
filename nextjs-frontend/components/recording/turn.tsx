"use client";

import { ScanFace } from "lucide-react";
import Link from "next/link";
import { memo, type MouseEvent, type ReactNode } from "react";

import type { EditTarget } from "@/components/recording/edit";
import { SegmentEditor, ReassignMenu } from "@/components/recording/edit";
import {
  entityRanges,
  showEmotion,
  splitRuns,
  type Segment,
  type SpeakerInfo,
  type Turn,
} from "@/components/recording/model";
import { EmotionChip, EventChip } from "@/components/ui/badge";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export type Unsure = { score: number; maybe: string };

/**
 * One turn: timestamp (seeks), the speaker's colour bar and name, and its segments as one serif paragraph. The line
 * being played gets a soft tint; entity names are dotted-underlined; find matches are highlighted.
 */
export const TurnView = memo(function TurnView({
  turn,
  segments,
  speaker,
  active,
  hits,
  currentHit,
  entityNames,
  unsure,
  onScreen,
  editing,
  editTarget,
  onSeek,
  onEdit,
  compact,
  dense,
}: {
  turn: Turn;
  segments: Segment[];
  speaker: SpeakerInfo | null;
  /** The playing segment if it's in this turn, else -1. */
  active: number;
  hits: Map<number, { start: number; end: number }[]>;
  currentHit: { seg: number; start: number } | null;
  entityNames: Map<number, string[]>;
  unsure: Unsure | null;
  /** Video: this voice's face is on screen in the recording. */
  onScreen?: boolean;
  editing: boolean;
  /** The segment being edited, if it's in this turn. */
  editTarget: EditTarget | null;
  onSeek: (ms: number) => void;
  onEdit: (seg: number) => void;
  compact?: boolean;
  /** The video page's narrow transcript column. */
  dense?: boolean;
}) {
  const color = speaker?.color ?? "var(--border)";
  const name = speaker?.name ?? "Speaker not yet assigned";
  const editingHere = editing && editTarget != null && turn.segs.includes(editTarget.seg);
  const click = (s: Segment) => (e: MouseEvent) => {
    if (editing) {
      onEdit(s.idx);
      return;
    }
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && (e.currentTarget as HTMLElement).contains(sel.anchorNode)) return;
    onSeek(s.t0);
  };

  const body = (
    <div className="min-w-0">
      <div className={cn("flex items-center gap-2", compact ? "mb-1 h-5" : "mb-0.5 h-[22px]")}>
        {compact && <span aria-hidden className="size-2 shrink-0 rounded-[2px]" style={{ background: color }} />}
        {speaker ? (
          <Link
            href={`/speakers/${speaker.id}`}
            className="text-[13px] font-bold leading-none hover:underline"
            style={{ color }}
          >
            {name}
          </Link>
        ) : (
          <span className="text-[13px] font-bold leading-none text-fg-muted">{name}</span>
        )}
        {compact && (
          <button
            type="button"
            onClick={() => onSeek(turn.t0)}
            aria-label={`Play from ${tc(turn.t0)}`}
            className="tabular text-[12px] font-medium leading-none text-fg-muted hover:text-fg"
          >
            {tc(turn.t0)}
          </button>
        )}
        {onScreen && (
          <span
            title="This voice's face is linked and on screen"
            className="inline-flex h-[18px] items-center gap-[3px] rounded-pill bg-surface-neutral px-1.5 text-[10.5px] font-semibold leading-none text-fg-secondary"
          >
            <ScanFace aria-hidden className="size-[11px]" /> on screen
          </span>
        )}
        {editing && <ReassignMenu turn={turn} current={speaker} />}
        {unsure && (
          <span
            title={`The voice match is unsure (${unsure.score.toFixed(2)}): this may be ${unsure.maybe}. Confirm or reassign in the Speakers tab.`}
            className="inline-flex h-5 items-center gap-[5px] whitespace-nowrap rounded-pill border border-gold-border bg-gold-surface px-2 text-[11px] font-semibold leading-none text-gold-dark"
          >
            <span aria-hidden className="size-[7px] rotate-45 rounded-[1px] bg-gold" />
            Unsure · {unsure.score.toFixed(2)} · may be {unsure.maybe}
          </span>
        )}
      </div>
      <p
        className={cn(
          "m-0 text-fg [text-wrap:pretty]",
          compact
            ? "font-serif text-[17px] leading-[1.6]"
            : dense
              ? "font-serif text-[16.5px] leading-[1.55]"
              : "transcript-text",
          editingHere && "rounded-sm border border-blue bg-background px-3 py-2",
        )}
      >
        {turn.segs.map((i) => {
          const s = segments[i];
          if (!s) return null;
          if (editingHere && editTarget?.seg === i) return <SegmentEditor key={i} seg={s} />;
          return (
            <SegmentText
              key={i}
              seg={s}
              active={active === i}
              hits={hits.get(i)}
              currentStart={currentHit?.seg === i ? currentHit.start : null}
              names={entityNames.get(i)}
              onClick={click(s)}
              editing={editing}
            />
          );
        })}
      </p>
    </div>
  );

  if (compact) return <article className="py-3.5 pb-1">{body}</article>;
  return (
    <article
      className={cn(
        "grid",
        dense
          ? "grid-cols-[44px_3px_minmax(0,1fr)] gap-x-2.5 py-2.5"
          : "grid-cols-[56px_3px_minmax(0,1fr)] gap-x-[13px] py-3",
      )}
      data-turn={turn.key}
    >
      <button
        type="button"
        onClick={() => onSeek(turn.t0)}
        aria-label={`Play from ${tc(turn.t0)}, ${name}`}
        className={cn(
          "tabular self-start rounded-xs text-right font-medium text-fg-muted hover:text-fg",
          dense ? "text-[11.5px] leading-5" : "text-[12px] leading-[22px]",
        )}
      >
        {tc(turn.t0)}
      </button>
      <span aria-hidden className="rounded-[2px] opacity-85" style={{ background: color }} />
      {body}
    </article>
  );
});

function SegmentText({
  seg,
  active,
  hits,
  currentStart,
  names,
  onClick,
  editing,
}: {
  seg: Segment;
  active: boolean;
  hits?: { start: number; end: number }[];
  currentStart: number | null;
  names?: string[];
  onClick: (e: MouseEvent) => void;
  editing: boolean;
}) {
  const ranges = [
    ...(hits ?? []).map((h) => ({
      ...h,
      kind: h.start === currentStart ? "hit-current" : "hit",
    })),
    ...(names?.length ? entityRanges(seg.text, names) : []),
  ];
  const runs = ranges.length ? splitRuns(seg.text, ranges) : [{ text: seg.text, kind: null }];
  let chips: ReactNode = null;
  if (showEmotion(seg.emotion) || seg.event) {
    chips = (
      <>
        {showEmotion(seg.emotion) && (
          <EmotionChip
            emotion={seg.emotion}
            className="mx-0.5 ml-1.5 border-transparent bg-surface-neutral align-[2px] leading-none"
          />
        )}
        {seg.event && <EventChip event={seg.event} className="mx-0.5 ml-1.5 align-[2px] leading-none" />}
      </>
    );
  }
  return (
    <>
      <span
        data-seg={seg.idx}
        onClick={onClick}
        className={cn(
          "rounded-[4px] [box-decoration-break:clone] [-webkit-box-decoration-break:clone]",
          editing ? "cursor-text hover:bg-blue-surface" : "cursor-pointer",
          active && "bg-hl py-0.5",
        )}
      >
        {runs.map((r, i) =>
          r.kind === "entity" ? (
            <span key={i} className="underline decoration-fg-muted decoration-dotted underline-offset-4">
              {r.text}
            </span>
          ) : r.kind === "hit" || r.kind === "hit-current" ? (
            <mark
              key={i}
              data-hit={r.kind === "hit-current" ? "current" : undefined}
              className={cn(
                "rounded-[3px] text-fg",
                r.kind === "hit-current"
                  ? "bg-gold-surface shadow-[0_0_0_2px_var(--aladdin-gold)]"
                  : "bg-hl-word shadow-[0_0_0_2px_var(--hl-word)]",
              )}
            >
              {r.text}
            </mark>
          ) : (
            <span key={i}>{r.text}</span>
          ),
        )}
        {chips}
      </span>{" "}
    </>
  );
}
