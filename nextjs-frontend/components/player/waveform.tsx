"use client";

import { memo, useCallback, useMemo, useRef, type KeyboardEvent, type PointerEvent } from "react";

import { usePlayerApi, usePlayerTick } from "@/components/player/media";
import { axisTicks, laneBars, speakerSpans, type Segment } from "@/components/recording/model";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export type WaveLane = { key: string; name: string; color: string };
export type WaveTick = { t: number; kind: "hit" | "mention"; tip: string };
/**
 * wave: loudness bars per speaker lane (audio with an envelope) · timeline: solid blocks per speaker from the
 * transcript's timestamps (no audio) · unsorted: one lane while speakers aren't separated yet (processing).
 */
export type WaveMode = "wave" | "timeline" | "unsorted";

const LABEL_W = 72;

/**
 * The waveform: one lane per speaker with numbered chapter lines, ▲ find hits and ● entity mentions, and the playhead.
 * It is the seek slider (aria-valuetext "14:32 of 47:18, Host B, chapter 5"): click or drag to seek, arrows ±5 s,
 * Page Up/Down ±1 min, Home/End.
 */
export function Waveform({
  mode,
  lanes,
  segments,
  envelope,
  durationMs,
  chapters,
  ticks,
  valueText,
  progress,
  compact,
  bars = 150,
  showPlayhead = true,
  className,
}: {
  mode: WaveMode;
  lanes: WaveLane[];
  segments: Segment[];
  envelope: number[] | null;
  durationMs: number;
  chapters: { t0: number; title: string }[];
  ticks: WaveTick[];
  /** aria-valuetext for a time. */
  valueText: (ms: number) => string;
  /** Unsorted mode: how far transcription has got (0..1). */
  progress?: number;
  compact?: boolean;
  bars?: number;
  showPlayhead?: boolean;
  className?: string;
}) {
  const api = usePlayerApi();
  const root = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const pct = useCallback(
    (ms: number) => (durationMs > 0 ? Math.max(0, Math.min(100, (ms / durationMs) * 100)) : 0),
    [durationMs],
  );

  usePlayerTick(
    useCallback(
      (ms: number) => {
        const r = root.current;
        if (!r) return;
        r.style.setProperty("--played", `${pct(ms)}%`);
        r.setAttribute("aria-valuenow", String(Math.round(ms / 1000)));
        r.setAttribute("aria-valuetext", valueText(ms));
      },
      [pct, valueText],
    ),
  );

  const seekAt = (clientX: number) => {
    const el = track.current;
    if (!el || durationMs <= 0) return;
    const b = el.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (clientX - b.left) / Math.max(1, b.width)));
    api.seek(f * durationMs, { manual: true });
  };
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    seekAt(e.clientX);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging.current) seekAt(e.clientX);
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step: Record<string, number> = {
      ArrowLeft: -5000,
      ArrowRight: 5000,
      ArrowDown: -5000,
      ArrowUp: 5000,
      PageDown: -60_000,
      PageUp: 60_000,
    };
    if (e.key in step) api.seekBy(step[e.key], { manual: true });
    else if (e.key === "Home") api.seek(0, { manual: true });
    else if (e.key === "End") api.seek(durationMs, { manual: true });
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const laneH = compact
    ? 14
    : mode === "unsorted"
      ? 44
      : mode === "timeline"
        ? 14
        : lanes.length <= 2
          ? 30
          : lanes.length <= 4
            ? 20
            : 12;
  const labelW = compact ? 0 : LABEL_W;
  const axis = useMemo(() => axisTicks(durationMs), [durationMs]);

  return (
    <div
      ref={root}
      role="slider"
      tabIndex={0}
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={Math.round(durationMs / 1000)}
      aria-valuenow={0}
      aria-valuetext={valueText(0)}
      onKeyDown={onKeyDown}
      className={cn("relative flex select-none flex-col gap-[3px] rounded-xs outline-offset-4", className)}
      style={{ ["--played" as string]: "0%" }}
    >
      {!compact && (
        <div aria-hidden className="relative h-3.5" style={{ marginLeft: labelW }}>
          {chapters.map((c, i) => (
            <span
              key={i}
              className="tabular absolute top-0 -translate-x-1/2 text-[9.5px] font-semibold leading-[14px] text-fg-muted"
              style={{ left: `${pct(c.t0)}%` }}
            >
              {i + 1}
            </span>
          ))}
        </div>
      )}
      <div
        className="relative flex flex-col gap-[3px]"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {lanes.map((ln, i) => (
          <div key={ln.key} className="flex items-center" style={{ height: laneH }}>
            {!compact && (
              <span
                className="flex shrink-0 items-center gap-[5px] overflow-hidden text-[11.5px] font-semibold leading-none text-fg-strong"
                style={{ width: labelW }}
                title={ln.name}
              >
                <span aria-hidden className="size-2 shrink-0 rounded-[2px]" style={{ background: ln.color }} />
                <span className="truncate pr-1">{ln.name}</span>
              </span>
            )}
            <div
              ref={i === 0 ? track : undefined}
              className={cn(
                "relative h-full flex-1 cursor-pointer overflow-hidden",
                mode === "timeline" && "rounded-xs bg-surface-neutral",
              )}
            >
              <LaneBody
                mode={mode}
                laneKey={ln.key}
                color={ln.color}
                segments={segments}
                envelope={envelope}
                durationMs={durationMs}
                bars={bars}
                laneKeys={lanes.map((l) => l.key)}
                progress={progress}
              />
            </div>
          </div>
        ))}
        {!compact && (
          <div aria-hidden className="pointer-events-none absolute inset-y-0 right-0" style={{ left: labelW }}>
            {chapters.map((c, i) => (
              <span key={i} className="absolute inset-y-0 w-px bg-border" style={{ left: `${pct(c.t0)}%` }} />
            ))}
          </div>
        )}
        {showPlayhead && (
          <div
            aria-hidden
            className="pointer-events-none absolute -bottom-[3px] -top-[3px] right-0"
            style={{ left: labelW }}
          >
            <span
              className={cn(
                "absolute inset-y-0 -ml-px w-0.5 rounded-[1px]",
                mode === "timeline" ? "bg-fg-secondary" : "bg-fg",
              )}
              style={{ left: "var(--played)" }}
            />
          </div>
        )}
      </div>
      {!compact && (
        <div className="relative h-3" style={{ marginLeft: labelW }}>
          {ticks.map((k, i) => (
            <span
              key={i}
              title={k.tip}
              className={cn(
                "absolute top-0.5 -translate-x-1/2 text-[9px] font-bold leading-none",
                k.kind === "hit" ? "text-blue" : "text-fg-muted",
              )}
              style={{ left: `${pct(k.t)}%` }}
            >
              <span aria-hidden>{k.kind === "hit" ? "▲" : "●"}</span>
              <span className="sr-only">{k.tip}</span>
            </span>
          ))}
        </div>
      )}
      {!compact && (
        <div
          aria-hidden
          className="tabular relative h-3 text-[10.5px] leading-none text-fg-muted"
          style={{ marginLeft: labelW }}
        >
          {axis.map((t, i) => (
            <span
              key={t}
              className={cn(
                "absolute top-0",
                i === 0 ? "" : i === axis.length - 1 ? "-translate-x-full" : "-translate-x-1/2",
              )}
              style={{ left: `${pct(t)}%` }}
            >
              {tc(t)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** One lane's bars (or blocks). Memoised: it only redraws when the data changes; playback just moves --played. */
const LaneBody = memo(function LaneBody({
  mode,
  laneKey,
  color,
  segments,
  envelope,
  durationMs,
  bars,
  laneKeys,
  progress,
}: {
  mode: WaveMode;
  laneKey: string;
  color: string;
  segments: Segment[];
  envelope: number[] | null;
  durationMs: number;
  bars: number;
  laneKeys: string[];
  progress?: number;
}) {
  const heights = useMemo(() => {
    if (mode === "timeline") return null;
    if (mode === "unsorted") {
      const n = bars;
      return Array.from({ length: n }, (_, i) => {
        if (!envelope?.length) return 0.08;
        const a = Math.floor((i / n) * envelope.length);
        const b = Math.max(a + 1, Math.floor(((i + 1) / n) * envelope.length));
        let h = 0;
        for (let j = a; j < b && j < envelope.length; j++) h = Math.max(h, envelope[j]);
        return Math.max(0.06, h);
      });
    }
    return laneBars(envelope, segments, durationMs, bars, laneKeys).get(laneKey) ?? [];
  }, [mode, bars, envelope, segments, durationMs, laneKeys, laneKey]);
  const spans = useMemo(
    () => (mode === "timeline" ? (speakerSpans(segments, durationMs).get(laneKey) ?? []) : []),
    [mode, segments, durationMs, laneKey],
  );

  if (mode === "timeline") {
    return (
      <>
        {spans.map(([a, b], i) => (
          <span
            key={i}
            className="absolute inset-y-0"
            style={{
              left: `${a * 100}%`,
              width: `${Math.max(0.25, (b - a) * 100)}%`,
              background: color,
            }}
          />
        ))}
      </>
    );
  }
  const n = heights?.length ?? 0;
  const svg = () => (
    <svg viewBox={`0 0 ${n} 100`} preserveAspectRatio="none" className="absolute inset-0 size-full" aria-hidden>
      <g style={{ fill: color }}>
        {heights?.map((h, i) =>
          h > 0 ? <rect key={i} x={i + 0.12} width={0.76} y={50 - h * 50} height={h * 100} rx={0.2} /> : null,
        )}
      </g>
      {mode === "wave" && (
        <g style={{ fill: color }} opacity={0.25}>
          {heights?.map((h, i) => (h > 0 ? null : <rect key={i} x={i + 0.12} width={0.76} y={47} height={6} />))}
        </g>
      )}
    </svg>
  );
  if (mode === "unsorted") {
    const done = Math.max(0, Math.min(1, progress ?? 1)) * 100;
    return (
      <>
        <div className="absolute inset-0 opacity-[.22]">{svg()}</div>
        <div className="absolute inset-0" style={{ clipPath: `inset(0 ${100 - done}% 0 0)` }}>
          {svg()}
        </div>
      </>
    );
  }
  return (
    <>
      <div className="absolute inset-0 opacity-[.42]">{svg()}</div>
      <div className="absolute inset-0" style={{ clipPath: "inset(0 calc(100% - var(--played)) 0 0)" }}>
        {svg()}
      </div>
    </>
  );
});
