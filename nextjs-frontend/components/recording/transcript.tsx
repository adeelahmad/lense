"use client";

import { LocateFixed, Pencil, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { prefersReducedMotion, usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { EditProvider, EditToolbar, useEdit } from "@/components/recording/edit";
import { FindBar } from "@/components/recording/find-bar";
import { transcriptOrigin } from "@/components/recording/labels";
import { commentsAt, highlightRanges, type HighlightRange } from "@/components/recording/comments-model";
import {
  useComments,
  useHighlights,
  useNamespaceFaces,
  useNotes,
  useSpeakerDirectory,
} from "@/components/recording/hooks";
import { currentStep } from "@/components/recording/jobs";
import { segmentAt, textRange, wordAt, type Segment } from "@/components/recording/model";
import { notesAt } from "@/components/recording/notes-model";
import { SelectionToolbar } from "@/components/recording/selection-toolbar";
import { TurnView, type Unsure } from "@/components/recording/turn";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Scroll `el` to the middle of `box` unless it's already comfortably in view. */
export function scrollIntoBox(box: HTMLElement, el: HTMLElement, force = false) {
  const b = box.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const top = r.top - b.top;
  if (!force && top > b.height * 0.15 && top + r.height < b.height * 0.7) return;
  box.scrollTo({
    top: box.scrollTop + top - b.height / 2 + Math.min(r.height, b.height / 2) / 2,
    behavior: prefersReducedMotion() ? "auto" : "smooth",
  });
}

/**
 * The transcript (R1, R3, R5): toolbar (find, follow playback, edit), turns, the selection toolbar, and edit mode.
 * Follow playback keeps the current line in view; scrolling by hand pauses it until it's pressed again.
 */
export function Transcript({ compact, slim, className }: { compact?: boolean; slim?: boolean; className?: string }) {
  return (
    <EditProvider>
      <TranscriptInner compact={compact} slim={slim} className={className} />
    </EditProvider>
  );
}

/** `compact`: phones (no toolbar, speaker line above the text). `slim`: the video page's narrow column. */
function TranscriptInner({ compact, slim, className }: { compact?: boolean; slim?: boolean; className?: string }) {
  const r = useRec();
  const { model, turns, speakers, find, editing, state, ns } = r;
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const edit = useEdit();
  const active = segmentAt(model.segments, time);
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const dir = useSpeakerDirectory(ns);
  // Video: voices whose face is linked and on screen in this recording get an "on screen" chip.
  const faces = useNamespaceFaces(ns, model.media.kind === "video" && model.facesMode === "recognize");
  const onScreen = useMemo(() => {
    const ids = new Set(model.faces.map((f) => f.face).filter(Boolean));
    return new Set(
      ((faces.data?.faces ?? []) as { id: number; speaker?: number | null }[])
        .filter((f) => ids.has(f.id) && f.speaker != null)
        .map((f) => `s${f.speaker}`),
    );
  }, [faces.data, model.faces]);

  const hitsBySeg = useMemo(() => {
    const m = new Map<number, { start: number; end: number }[]>();
    for (const h of find.hits) m.set(h.seg, [...(m.get(h.seg) ?? []), { start: h.start, end: h.end }]);
    return m;
  }, [find.hits]);
  const currentHit = find.hits[find.index] ?? null;
  useSpokenWord(box, model.segments);
  // Turns that notes are about get a mark that opens the Notes tab.
  const notes = useNotes(r.id).data;
  const noteCounts = useMemo(() => {
    const m = new Map<number, number>();
    for (const t of notes?.length ? turns : []) {
      const n = notesAt(notes ?? [], t.t0, t.t1).length;
      if (n) m.set(t.key, n);
    }
    return m;
  }, [notes, turns]);
  const { setTab, focusHighlight } = r;
  const openNotes = useCallback(() => setTab("notes"), [setTab]);
  // Turns with comment threads get a mark that opens the Comments tab.
  const comments = useComments(r.id).data;
  const commentCounts = useMemo(() => {
    const m = new Map<number, number>();
    for (const t of comments?.length ? turns : []) {
      const n = commentsAt(comments ?? [], t.t0, t.t1).length;
      if (n) m.set(t.key, n);
    }
    return m;
  }, [comments, turns]);
  const openComments = useCallback(() => setTab("comments"), [setTab]);
  // Highlighted passages are marked in their colour, by segment.
  const highlights = useHighlights(r.id).data;
  const highlightsBySeg = useMemo(() => {
    const m = new Map<number, HighlightRange[]>();
    if (!highlights?.length) return m;
    for (const s of model.segments) {
      const ranges = highlightRanges(s, highlights);
      if (ranges.length) m.set(s.idx, ranges);
    }
    return m;
  }, [highlights, model.segments]);
  const entityNames = useMemo(() => {
    const m = new Map<number, string[]>();
    for (const e of model.entities) for (const s of e.segs) m.set(s, [...(m.get(s) ?? []), e.name]);
    return m;
  }, [model.entities]);
  // Speakers the voice match wasn't sure about: the registry suggests who they may be.
  const unsure = useMemo(() => {
    const m = new Map<string, Unsure>();
    for (const s of dir.data?.speakers ?? []) {
      const best = [...(s.suggestions ?? [])].sort((a, b) => b.score - a.score)[0];
      if (best) m.set(`s${s.id}`, { score: best.score, maybe: best.name });
    }
    return m;
  }, [dir.data]);

  // Follow playback.
  useEffect(() => {
    if (!follow || active < 0 || !box.current) return;
    const el = box.current.querySelector<HTMLElement>(`[data-seg="${active}"]`);
    if (el) scrollIntoBox(box.current, el);
  }, [active, follow]);
  // Find: bring the current match into view.
  useEffect(() => {
    if (!currentHit || !box.current) return;
    const el =
      box.current.querySelector<HTMLElement>(`[data-seg="${currentHit.seg}"] mark[data-hit="current"]`) ??
      box.current.querySelector<HTMLElement>(`[data-seg="${currentHit.seg}"]`);
    if (el) scrollIntoBox(box.current, el, true);
  }, [currentHit]);

  const stopFollow = useCallback(() => setFollow(false), []);
  const onSeek = useCallback((ms: number) => api.seek(ms, { manual: true }), [api]);
  const onEdit = useCallback((seg: number) => edit?.setTarget({ seg }), [edit]);
  const transcribing = state.phase === "processing" && state.job && currentStep(state.job) === "transcribe";

  const meta =
    state.phase === "processing"
      ? `Live · ${model.segments.length} segments`
      : [
          `${model.segments.length} segments`,
          transcriptOrigin(r.rec.engine),
          r.rec.language && !["none", "nospeech"].includes(r.rec.language) ? r.rec.language.toUpperCase() : null,
        ]
          .filter(Boolean)
          .join(" · ");

  return (
    <div className={cn("relative flex min-h-0 flex-1 flex-col", className)}>
      {!compact && (
        <div
          className={cn(
            "flex h-12 shrink-0 items-center gap-2.5 border-b border-border",
            slim ? "px-[18px]" : "px-6",
            editing ? "bg-blue-surface" : "bg-background",
          )}
        >
          {editing ? (
            <EditToolbar />
          ) : find.open ? (
            <FindBar />
          ) : slim ? (
            <>
              <h2 className="text-[13px] font-bold leading-none text-fg">Transcript</h2>
              <span className="min-w-0 flex-1 truncate text-[12px] leading-none text-fg-muted">
                from the soundtrack
              </span>
              <Tooltip content="Find in the transcript (/)">
                <button
                  type="button"
                  aria-label="Find in the transcript"
                  onClick={() => find.setOpen(true)}
                  className="grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
                >
                  <Search className="size-3.5" />
                </button>
              </Tooltip>
              <FollowButton follow={follow} setFollow={setFollow} box={box} active={active} slim />
              <Tooltip
                content={
                  r.canEdit ? "Edit the transcript" : `Viewers can't edit transcripts. ${needRole("editor", ns)}.`
                }
              >
                <button
                  type="button"
                  aria-label="Edit the transcript"
                  aria-disabled={!r.canEdit || undefined}
                  onClick={() => r.canEdit && model.segments.length && r.setEditing(true)}
                  className={cn(
                    "grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral",
                    !r.canEdit && "cursor-not-allowed opacity-50",
                  )}
                >
                  <Pencil className="size-3.5" />
                </button>
              </Tooltip>
            </>
          ) : (
            <>
              <h2 className="text-[13px] font-bold leading-none text-fg">Transcript</h2>
              <span className="tabular min-w-0 truncate text-[12px] leading-none text-fg-muted">{meta}</span>
              <span className="flex-1" />
              <button
                type="button"
                onClick={() => find.setOpen(true)}
                className="flex h-7 items-center gap-1.5 rounded-pill px-2.5 text-[12.5px] font-medium text-fg-secondary hover:bg-surface-neutral"
              >
                <Search className="size-3.5" /> Find{" "}
                <kbd className="rounded-xs border border-border px-[5px] py-0.5 font-sans text-[10.5px] font-medium leading-none">
                  /
                </kbd>
              </button>
              <FollowButton follow={follow} setFollow={setFollow} box={box} active={active} />
              <Button
                variant="secondary"
                size="sm"
                icon={<Pencil />}
                disabled={!r.canEdit || !model.segments.length}
                disabledReason={
                  !r.canEdit
                    ? `Viewers can't edit transcripts. ${needRole("editor", ns)}.`
                    : "There's no transcript to edit yet"
                }
                onClick={() => r.setEditing(true)}
              >
                Edit
              </Button>
            </>
          )}
        </div>
      )}
      {compact && find.open && (
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
          <FindBar />
        </div>
      )}
      <div
        ref={box}
        tabIndex={-1}
        onWheel={stopFollow}
        onTouchMove={stopFollow}
        onKeyDown={(e) => ["PageUp", "PageDown", "Home", "End"].includes(e.key) && stopFollow()}
        className={cn(
          "min-h-0 flex-1 overflow-y-auto outline-none",
          compact ? "px-[18px] pb-4 pt-1" : slim ? "pb-10 pl-2 pr-[18px] pt-1" : "pb-10 pl-4 pr-6 pt-1",
        )}
        aria-label="Transcript"
        role="region"
      >
        {!model.segments.length ? (
          state.phase === "processing" ? (
            <div className="flex flex-col gap-3 px-2 py-6" aria-busy="true">
              <p className="flex items-center gap-2 text-[13px] text-fg-secondary">
                <span aria-hidden className="size-2 rounded-full bg-blue" /> Transcribing — lines appear here as they
                arrive.
              </p>
              {[82, 64, 74, 58].map((w, i) => (
                <Skeleton key={i} style={{ width: `${w}%` }} />
              ))}
            </div>
          ) : (
            <EmptyState title="No transcript yet">
              {model.audio
                ? "Run Transcribe (Reprocess → Choose steps) to make one from the audio."
                : "This recording has no transcript lines."}
            </EmptyState>
          )
        ) : (
          turns.map((t) => (
            <TurnView
              key={t.key}
              turn={t}
              segments={model.segments}
              speaker={t.speaker ? (speakers.get(t.speaker) ?? null) : null}
              active={active >= t.segs[0] && active <= t.segs[t.segs.length - 1] ? active : -1}
              hits={hitsBySeg}
              currentHit={
                currentHit && currentHit.seg >= t.segs[0] && currentHit.seg <= t.segs[t.segs.length - 1]
                  ? currentHit
                  : null
              }
              entityNames={entityNames}
              unsure={t.speaker ? (unsure.get(t.speaker) ?? null) : null}
              onScreen={Boolean(t.speaker && onScreen.has(t.speaker))}
              notes={noteCounts.get(t.key)}
              onNotes={openNotes}
              comments={commentCounts.get(t.key)}
              onComments={openComments}
              highlights={highlightsBySeg}
              onHighlight={focusHighlight}
              editing={editing}
              editTarget={edit?.target ?? null}
              onSeek={onSeek}
              onEdit={onEdit}
              compact={compact}
              dense={slim}
            />
          ))
        )}
        {transcribing && model.segments.length > 0 && (
          <p className="ml-[72px] mt-1.5 flex items-center gap-2 text-[12.5px] font-medium text-fg-secondary">
            <span aria-hidden className="size-2 rounded-full bg-blue" />
            Transcribing — new lines appear here as they arrive ({model.segments.length} so far)
          </p>
        )}
      </div>
      {!editing && <SelectionToolbar box={box} />}
      {compact && !follow && active >= 0 && (
        <button
          type="button"
          onClick={() => setFollow(true)}
          className="absolute bottom-3 left-1/2 flex h-8 -translate-x-1/2 items-center gap-1.5 rounded-pill bg-blue px-3 text-[12.5px] font-semibold text-white shadow-2"
        >
          <LocateFixed className="size-3.5" /> Back to the playing line
        </button>
      )}
    </div>
  );
}

function FollowButton({
  follow,
  setFollow,
  box,
  active,
  slim,
}: {
  follow: boolean;
  setFollow: (f: boolean) => void;
  box: React.RefObject<HTMLDivElement | null>;
  active: number;
  slim?: boolean;
}) {
  return (
    <Tooltip
      content={
        follow
          ? "The transcript follows playback; scrolling pauses it"
          : "Scroll back to the playing line and follow it"
      }
    >
      <button
        type="button"
        aria-pressed={follow}
        onClick={() => {
          setFollow(!follow);
          if (!follow && box.current && active >= 0) {
            const el = box.current.querySelector<HTMLElement>(`[data-seg="${active}"]`);
            if (el) scrollIntoBox(box.current, el, true);
          }
        }}
        className={cn(
          "flex h-7 items-center gap-1.5 whitespace-nowrap rounded-pill px-2.5 text-[12.5px]",
          follow
            ? "bg-blue-surface font-semibold text-fg-accent"
            : "font-medium text-fg-secondary hover:bg-surface-neutral",
        )}
      >
        {slim ? (
          <>
            follow <span aria-hidden className={cn("size-2 rounded-full", follow ? "bg-blue" : "bg-fg-muted")} />
          </>
        ) : (
          <>
            <LocateFixed className="size-3.5" /> Follow playback
          </>
        )}
      </button>
    </Tooltip>
  );
}

/**
 * The word being said gets a highlight, in lines whose words are timed (the CSS Custom Highlight API, so the text isn't
 * re-rendered as the words go by; browsers without it keep the line's tint).
 */
function useSpokenWord(box: React.RefObject<HTMLDivElement | null>, segments: Segment[]) {
  const api = usePlayerApi();
  useEffect(() => {
    if (typeof CSS === "undefined" || !CSS.highlights || typeof Highlight === "undefined") return;
    if (!segments.some((s) => s.words)) return;
    const hl = new Highlight();
    CSS.highlights.set("lens-word", hl);
    let last = "";
    const paint = (ms: number) => {
      const i = segmentAt(segments, ms);
      const words = segments[i]?.words;
      const w = words ? wordAt(words, ms) : -1;
      const key = w < 0 ? "" : `${i}:${w}`;
      if (key === last) return;
      last = key;
      hl.clear();
      const el = words && w >= 0 ? box.current?.querySelector<HTMLElement>(`[data-seg="${i}"]`) : null;
      const range = el && words ? textRange(el, words[w][0], words[w][1]) : null;
      if (range) hl.add(range);
    };
    paint(api.now());
    const off = api.subscribe(paint);
    return () => {
      off();
      CSS.highlights.delete("lens-word");
    };
  }, [api, box, segments]);
}
