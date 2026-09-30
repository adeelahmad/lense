"use client";

import { Minus, Plus } from "lucide-react";
import { useCallback, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { usePlayerApi, usePlayerState, usePlayerTick } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { ariaTimeText, chapterAt, segmentAt } from "@/components/recording/model";
import { blocks, faceLanes, initialZoom, shotAt, timelineWindow, voiceSpans } from "@/components/recording/video/model";
import { useFaceColors } from "@/components/recording/video/stage";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

type Lane = {
  key: string;
  label: string;
  title?: string;
  dot: string;
  round?: boolean;
  h: number;
  blocks: {
    l: number;
    w: number;
    cls?: string;
    style?: React.CSSProperties;
    tip?: string;
  }[];
};

const LABEL = 112;

/** "Mara Quint" → "Mara" for lane labels; generic labels ("Speaker 3", "Face 2") stay whole. */
export function shortName(name: string): string {
  return /^(speaker|face|person)\s+\d+$/i.test(name.trim()) ? name.trim() : (name.trim().split(/\s+/)[0] ?? name);
}

/**
 * The video timeline (VR1): lanes for shots, each voice, each person on screen (up to eight), text on screen,
 * chapters and find hits, with a playhead. Zoom narrows the window around the playhead; long videos open zoomed in
 * with an overview strip to pan. Hover shows the shot's frame; click seeks. It is the seek slider for keyboards.
 */
export function VideoTimeline({ compact }: { compact?: boolean }) {
  const { model, speakers, find } = useRec();
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const color = useFaceColors();
  const dur = Math.max(1, model.durationMs);
  const [zoom, setZoom] = useState(() => initialZoom(dur));
  const [center, setCenter] = useState<number | null>(null);
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  // The window follows the playhead unless someone panned it.
  const z = compact ? 1 : zoom;
  const focus = z === 1 ? 0 : (center ?? time);
  const win = useMemo(() => timelineWindow(dur, z, focus), [dur, z, focus]);
  const curShot = compact ? shotAt(model.shots, time) : -1;
  const [a, b] = win;
  const len = b - a;

  usePlayerTick(
    useCallback(
      (ms: number) => {
        const el = root.current;
        if (!el) return;
        const f = (ms - a) / len;
        el.style.setProperty("--ph", String(Math.max(0, Math.min(1, f))));
        el.style.setProperty("--ph-vis", f < 0 || f > 1 ? "hidden" : "visible");
      },
      [a, len],
    ),
  );

  const lanes = useMemo<Lane[]>(() => {
    const out: Lane[] = [];
    if (model.shots.length) {
      out.push({
        key: "shots",
        label: "Shots",
        dot: "var(--text-strong)",
        h: 22,
        blocks: blocks(
          model.shots.map((s) => [s.t0, s.t1] as [number, number]),
          win,
          0.2,
        ).map(([l, w], i) => ({
          l,
          w,
          cls: cn("border-r-2 border-background", i === curShot ? "bg-blue" : i % 2 ? "bg-fg-strong" : "bg-fg-muted"),
        })),
      });
    }
    const voices = voiceSpans(model.segments);
    for (const s of model.speakers) {
      const spans = voices.get(s.key);
      if (!spans) continue;
      out.push({
        key: `v-${s.key}`,
        label: `${shortName(s.name)} (voice)`,
        title: `${s.name}'s voice`,
        dot: s.color,
        h: 12,
        blocks: blocks(spans, win).map(([l, w]) => ({
          l,
          w,
          style: { background: s.color },
        })),
      });
    }
    if (model.facesMode !== "off") {
      const { lanes: people } = faceLanes(model.faces);
      people.forEach((f) => {
        const i = model.faces.indexOf(f);
        out.push({
          key: `f-${f.id}`,
          label: model.facesMode === "recognize" ? `${shortName(f.name)} on screen` : `Face ${i + 1}`,
          title: model.facesMode === "recognize" ? `${f.name} on screen` : undefined,
          dot: color(i),
          round: true,
          h: 10,
          blocks: blocks(f.spans, win).map(([l, w]) => ({
            l,
            w,
            style: { background: color(i), opacity: 0.45 },
          })),
        });
      });
    }
    if (model.screenText.length)
      out.push({
        key: "ocr",
        label: "Text on screen",
        dot: "var(--aladdin-gold)",
        h: 12,
        blocks: blocks(
          model.screenText.map((s) => [s.t0, s.t1] as [number, number]),
          win,
        ).map(([l, w]) => ({
          l,
          w,
          style: { background: "var(--aladdin-gold)", opacity: 0.55 },
        })),
      });
    if (model.chapters.length)
      out.push({
        key: "ch",
        label: "Chapters",
        dot: "var(--text-muted)",
        h: 12,
        blocks: blocks(
          model.chapters.map((c) => [c.t0, c.t1] as [number, number]),
          win,
        ).map(([l, w], i) => ({
          l,
          w,
          cls: cn("border-r border-border", i % 2 ? "bg-surface-neutral" : "bg-border"),
          tip: model.chapters[i]?.title,
        })),
      });
    if (find.hits.length)
      out.push({
        key: "hits",
        label: "Find hits",
        dot: "var(--aladdin-blue)",
        h: 10,
        blocks: blocks(
          find.hits.map(
            (h) => [model.segments[h.seg]?.t0 ?? 0, (model.segments[h.seg]?.t0 ?? 0) + 1] as [number, number],
          ),
          win,
          0.6,
        ).map(([l, w]) => ({ l, w, cls: "bg-blue" })),
      });
    return compact ? out.filter((l) => l.key === "shots" || l.key.startsWith("v-")).slice(0, 1) : out;
  }, [model, win, color, find.hits, compact, curShot]);

  const at = (clientX: number) => {
    const r = track.current?.getBoundingClientRect();
    if (!r) return a;
    return a + Math.max(0, Math.min(1, (clientX - r.left) / Math.max(1, r.width))) * len;
  };
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    api.seek(at(e.clientX), { manual: true });
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const r = track.current?.getBoundingClientRect();
    if (r) setHover({ x: e.clientX - r.left, t: at(e.clientX) });
    if (dragging.current) api.seek(at(e.clientX), { manual: true });
  };
  const onKey = (e: KeyboardEvent) => {
    const step: Record<string, number> = {
      ArrowLeft: -5000,
      ArrowRight: 5000,
      PageDown: -60_000,
      PageUp: 60_000,
    };
    if (e.key in step) api.seekBy(step[e.key], { manual: true });
    else if (e.key === "Home") api.seek(0, { manual: true });
    else if (e.key === "End") api.seek(dur, { manual: true });
    else if (e.key === "+" || e.key === "=") setZoom((z) => Math.min(64, z * 2));
    else if (e.key === "-") setZoom((z) => Math.max(1, z / 2));
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  const hoverShot = hover ? shotAt(model.shots, hover.t) : -1;
  const frame = hoverShot >= 0 ? model.shots[hoverShot].frame : null;
  const si = segmentAt(model.segments, time);
  const valueText = ariaTimeText(
    time,
    dur,
    si >= 0 && model.segments[si].speaker ? speakers.get(model.segments[si].speaker!)?.name : null,
    chapterAt(model.chapters, time),
  );

  return (
    <div
      ref={root}
      role="slider"
      tabIndex={0}
      aria-label={`Timeline, showing ${tc(a)}–${tc(b)}. Plus and minus zoom.`}
      aria-valuemin={0}
      aria-valuemax={Math.round(dur / 1000)}
      aria-valuenow={Math.round(time / 1000)}
      aria-valuetext={valueText}
      onKeyDown={onKey}
      className={cn(
        "relative flex flex-col gap-[3px]",
        compact ? "rounded-sm" : "rounded-[10px] border border-border px-2.5 pb-1.5 pt-2",
      )}
      style={{ ["--ph" as string]: "0" }}
    >
      <div
        className={cn(
          "tabular flex items-center gap-2 pb-1 text-[11px] font-medium leading-none text-fg-muted",
          compact && "hidden",
        )}
      >
        <span className="flex-1">{tc(a)}</span>
        <span>zoom</span>
        <span className="flex overflow-hidden rounded-[6px] border border-border">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={zoom <= 1}
            onClick={() => setZoom((z) => Math.max(1, z / 2))}
            className="grid h-5 w-6 place-items-center hover:bg-surface-neutral disabled:opacity-40"
          >
            <Minus className="size-3" />
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={zoom >= 64}
            onClick={() => setZoom((z) => Math.min(64, z * 2))}
            className="grid h-5 w-6 place-items-center border-l border-border hover:bg-surface-neutral disabled:opacity-40"
          >
            <Plus className="size-3" />
          </button>
        </span>
        <span className="flex-1 text-right">{tc(b)}</span>
      </div>
      {z > 1 && <Overview dur={dur} win={win} onPan={(c) => setCenter(c)} onRelease={() => setCenter(null)} />}
      <div
        className="relative flex flex-col gap-[3px]"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={() => (dragging.current = false)}
        onPointerLeave={() => setHover(null)}
      >
        {lanes.map((ln, i) => (
          <div
            key={ln.key}
            className={cn("grid items-center", !compact && "gap-2")}
            style={{
              gridTemplateColumns: compact ? "minmax(0,1fr)" : `${LABEL - 8}px minmax(0,1fr)`,
              height: compact ? 26 : ln.h,
            }}
          >
            <span
              className={cn(
                "flex items-center gap-[5px] overflow-hidden whitespace-nowrap text-[11px] font-semibold leading-none text-fg-strong",
                compact && "sr-only",
              )}
              title={ln.title ?? ln.label}
            >
              <span
                aria-hidden
                className={cn("size-2 shrink-0", ln.round ? "rounded-full" : "rounded-[2px]")}
                style={{ background: ln.dot }}
              />
              <span className="truncate">{ln.label}</span>
            </span>
            <div
              ref={i === 0 ? track : undefined}
              className="relative h-full cursor-pointer overflow-hidden rounded-[3px] bg-surface"
            >
              {ln.blocks.map((bl, j) => (
                <span
                  key={j}
                  title={bl.tip}
                  className={cn("absolute inset-y-0 rounded-[2px]", bl.cls)}
                  style={{ left: `${bl.l}%`, width: `${bl.w}%`, ...bl.style }}
                />
              ))}
            </div>
          </div>
        ))}
        {!lanes.length && <p className="py-2 text-[12px] text-fg-muted">Nothing to show on the timeline yet.</p>}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-fg"
          style={{
            left: compact ? "calc(100% * var(--ph))" : `calc(${LABEL}px + (100% - ${LABEL}px) * var(--ph))`,
            visibility: "var(--ph-vis)" as never,
          }}
        />
        {hover && !compact && (
          <div
            className="pointer-events-none absolute top-6 z-10 flex w-[150px] -translate-x-1/2 flex-col gap-1 rounded-sm border border-border bg-background p-[5px] shadow-3"
            style={{ left: Math.max(75, LABEL + hover.x) }}
          >
            <div className="aspect-video overflow-hidden rounded-xs bg-black">
              {frame && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={frame} alt="" className="size-full object-cover" />
              )}
            </div>
            <span className="tabular text-[11px] font-semibold leading-none">
              {tc(hover.t)}
              {hoverShot >= 0 && ` · shot ${hoverShot + 1}`}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

/** The whole video in a thin strip, with the zoomed window in blue: drag it to pan. */
function Overview({
  dur,
  win,
  onPan,
  onRelease,
}: {
  dur: number;
  win: [number, number];
  onPan: (center: number) => void;
  onRelease: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const toCenter = (x: number) => {
    const r = ref.current?.getBoundingClientRect();
    return r ? Math.max(0, Math.min(1, (x - r.left) / r.width)) * dur : 0;
  };
  return (
    <div className="grid items-center gap-2 pb-1" style={{ gridTemplateColumns: `${LABEL - 8}px minmax(0,1fr)` }}>
      <span className="text-[11px] font-semibold text-fg-strong">Overview</span>
      <div
        ref={ref}
        className="relative h-2.5 cursor-grab rounded-[3px] bg-blue-border/50"
        onPointerDown={(e) => {
          drag.current = true;
          e.currentTarget.setPointerCapture?.(e.pointerId);
          onPan(toCenter(e.clientX));
          e.stopPropagation();
        }}
        onPointerMove={(e) => drag.current && onPan(toCenter(e.clientX))}
        onPointerUp={() => {
          drag.current = false;
        }}
        onDoubleClick={onRelease}
        title="Drag the window to pan; double-click to follow the playhead again"
      >
        <span
          className="absolute inset-y-0 rounded-[3px] bg-blue"
          style={{
            left: `${(win[0] / dur) * 100}%`,
            width: `${Math.max(1, ((win[1] - win[0]) / dur) * 100)}%`,
          }}
        />
      </div>
    </div>
  );
}
