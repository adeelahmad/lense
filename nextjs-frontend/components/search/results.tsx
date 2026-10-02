"use client";

import { Loader2, Pause, Play, Sparkle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { forwardRef, type KeyboardEvent } from "react";

import type { SearchHit } from "@/app/openapi-client/types.gen";
import type { HitGroup } from "@/components/search/facets";
import { ROLE_LABEL, hitHref, type FileRole } from "@/components/recording/files-model";
import { foundAs, onPage, recordingHref, relevanceLabel } from "@/components/search/links";
import type { InlinePlayer } from "@/components/search/player";
import { splitSnippet } from "@/components/search/snippet";
import { speakerTone } from "@/components/speakers/format";
import { Tooltip } from "@/components/ui/tooltip";
import { plural, shortDate, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Snippet text with the matched words highlighted (as text, never as HTML). */
export function Snippet({ html, className }: { html: string; className?: string }) {
  return (
    <span className={className}>
      {splitSnippet(html).map((p, i) =>
        p.mark ? (
          <mark key={i} className="rounded-[3px] bg-gold-surface px-0.5 font-semibold text-fg">
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </span>
  );
}

function focusSibling(from: HTMLElement, step: 1 | -1) {
  const list = from.closest("[data-hit-list]");
  if (!list) return;
  const rows = Array.from(list.querySelectorAll<HTMLElement>("[data-hit]"));
  const i = rows.indexOf(from);
  const next = rows[i + step];
  if (next) next.focus();
  else if (step === -1)
    (
      document.querySelector<HTMLInputElement>("[data-search-input] input, input[aria-label='Search transcripts']") ??
      undefined
    )?.focus();
}

function PlayButton({ hit, player, audio }: { hit: SearchHit; player: InlinePlayer; audio: boolean | undefined }) {
  const key = String(hit.id);
  const playing = player.isPlaying(key);
  const untimed = hit.t0 == null;
  const paged = onPage(hit);
  const none = untimed || paged || audio === false || player.noAudio.has(hit.recording_id);
  const label = paged
    ? `Nothing to play: ${(foundAs(hit) ?? "on a page").toLowerCase()}`
    : untimed
      ? "No time: a line of a file without times"
      : none
        ? "No audio — transcript only"
        : `${playing ? "Pause" : "Play"} from ${tc(hit.t0)}`;
  const btn = (
    <button
      type="button"
      tabIndex={-1}
      aria-label={label}
      aria-disabled={none || undefined}
      onClick={(e) => {
        e.stopPropagation();
        if (!none)
          player.play({
            key,
            recordingId: hit.recording_id,
            t0: hit.t0 ?? 0,
            title: hit.title,
            speaker: hit.speaker,
          });
      }}
      className={cn(
        "grid size-[26px] place-items-center self-center rounded-full bg-surface-neutral text-fg transition-colors duration-fast [&_svg]:size-3",
        none ? "cursor-not-allowed opacity-40" : "hover:bg-blue-surface hover:text-blue",
        playing && "bg-blue text-white hover:bg-blue-dark hover:text-white",
      )}
    >
      {player.loading === key ? (
        <Loader2 className="animate-spin" />
      ) : playing ? (
        <Pause />
      ) : (
        <Play className="translate-x-px" />
      )}
    </button>
  );
  return none ? (
    <Tooltip
      content={
        paged
          ? `${foundAs(hit) ?? "On the page"}, of a document or an image`
          : untimed
            ? "This line's file doesn't say when it is"
            : "No audio: this recording is a transcript"
      }
    >
      {btn}
    </Tooltip>
  ) : (
    btn
  );
}

function HitRow({
  hit,
  first,
  player,
  audio,
}: {
  hit: SearchHit;
  first: boolean;
  player: InlinePlayer;
  audio: boolean | undefined;
}) {
  const router = useRouter();
  const href = hitHref(hit);
  const paged = onPage(hit);
  const untimed = hit.t0 == null || paged;
  const at = paged ? `Page ${(hit.page ?? 0) + 1}` : hit.t0 == null ? `Line ${(hit.line ?? 0) + 1}` : tc(hit.t0);
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      focusSibling(e.currentTarget, e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) window.open(href, "_blank");
      else router.push(href);
    } else if (e.key === " ") {
      e.preventDefault();
      if (!untimed && audio !== false && !player.noAudio.has(hit.recording_id))
        player.play({
          key: String(hit.id),
          recordingId: hit.recording_id,
          t0: hit.t0 ?? 0,
          title: hit.title,
          speaker: hit.speaker,
        });
    }
  };
  const found = foundAs(hit);
  const where = found ? `, ${found.toLowerCase()}` : hit.source === "file" ? `, in ${hit.file_label ?? "a file"}` : "";
  const color = speakerTone(hit.speaker_id);
  return (
    <div
      data-hit
      tabIndex={first ? 0 : -1}
      onKeyDown={onKey}
      aria-label={`${paged ? at : untimed ? "No time" : tc(hit.t0)}${hit.speaker ? `, ${hit.speaker}` : ""}${where}. Enter opens${untimed ? "" : ", Space plays"}.`}
      className="-mx-2 grid grid-cols-[30px_54px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1 rounded-sm px-2 py-1 outline-none focus-visible:bg-hl focus-visible:ring-2 focus-visible:ring-blue md:grid-cols-[30px_54px_96px_minmax(0,1fr)]"
    >
      <PlayButton hit={hit} player={player} audio={audio} />
      <Link
        href={href}
        tabIndex={-1}
        className="tabular text-[12.5px] font-semibold text-fg-secondary hover:text-fg-accent hover:underline"
      >
        {at}
      </Link>
      <span className="flex min-w-0 items-center gap-[5px] text-[12px] font-semibold" style={{ color }}>
        {found ? (
          <span className="text-fg-secondary">{found}</span>
        ) : hit.source === "file" ? (
          <span
            className="truncate text-fg-secondary"
            title={`${hit.file_role ? ROLE_LABEL[hit.file_role as FileRole] : "File"}: ${hit.file_label ?? ""}`}
          >
            {hit.file_label ?? "A file"}
          </span>
        ) : (
          <>
            <span aria-hidden className="size-2 shrink-0 rounded-[2px]" style={{ background: color }} />
            <span className="truncate">{hit.speaker ?? "Unknown"}</span>
          </>
        )}
      </span>
      <span className="col-span-3 font-serif text-[15.5px] leading-[1.5] text-fg [text-wrap:pretty] md:col-span-1">
        {hit.match === "meaning" && (
          <Tooltip
            content={`Found by meaning${hit.similarity != null ? ` (${Math.round(hit.similarity * 100)}% alike)` : ""}: it's about what you searched for, in other words`}
          >
            <span className="mr-1.5 inline-flex translate-y-[-1px] items-center gap-1 rounded-pill bg-blue-surface px-1.5 align-middle font-sans text-[10.5px] font-bold uppercase tracking-[.04em] text-blue-dark">
              <Sparkle aria-hidden className="size-2.5" />
              Related
            </span>
          </Tooltip>
        )}
        <Snippet html={hit.snippet} />
        {hit.relevance != null && (
          <Tooltip content="How likely this answers what you searched for, as the decision model judged it. The best hits are in this order">
            <span className="tabular ml-1.5 inline-flex translate-y-[-1px] items-center rounded-pill border border-border px-1.5 align-middle font-sans text-[10.5px] font-semibold text-fg-secondary">
              {relevanceLabel(hit.relevance)}
            </span>
          </Tooltip>
        )}
      </span>
    </div>
  );
}

/** Results grouped by recording. ↓/↑ move between moments, Enter opens the recording there, Space plays. */
export const ResultGroups = forwardRef<
  HTMLDivElement,
  {
    groups: HitGroup[];
    player: InlinePlayer;
    audioOf: (recordingId: number) => boolean | undefined;
    className?: string;
  }
>(function ResultGroups({ groups, player, audioOf, className }, ref) {
  let first = true;
  return (
    <div ref={ref} data-hit-list role="list" aria-label="Search results" className={className}>
      {groups.map((g) => (
        <section
          key={g.recordingId}
          role="listitem"
          aria-label={g.title}
          className="flex flex-col gap-2 border-b border-border py-3.5"
        >
          <header className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
            <Link
              href={recordingHref(g.recordingId)}
              className="text-[15px] font-bold leading-snug text-fg hover:text-fg-accent hover:underline"
            >
              {g.title}
            </Link>
            <span className="text-[12.5px] text-fg-muted">
              {[g.namespace, g.recordedAt ? shortDate(g.recordedAt) : null].filter(Boolean).join(" · ")}
            </span>
            <span className="flex-1" />
            <span className="text-[12px] font-semibold text-fg-secondary">{plural(g.hits.length, "moment")}</span>
          </header>
          <div className="flex flex-col gap-1">
            {g.hits.map((h) => {
              const row = (
                <HitRow key={String(h.id)} hit={h} first={first} player={player} audio={audioOf(h.recording_id)} />
              );
              first = false;
              return row;
            })}
          </div>
        </section>
      ))}
    </div>
  );
});
