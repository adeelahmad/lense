"use client";

import {
  Captions,
  Columns2,
  PictureInPicture2,
  RectangleHorizontal,
  Rows2,
  ScanFace,
  ScanText,
  StepBack,
  StepForward,
} from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { usePlayerApi, usePlayerState, usePlayerTick } from "@/components/player/media";
import { PlayButton, SpeedMenu } from "@/components/player/transport";
import { useRec } from "@/components/recording/context";
import { segmentAt } from "@/components/recording/model";
import { useFaceColors } from "@/components/recording/video/face-colors";
import { faceBoxAt, fineTime, frameMs, textAt } from "@/components/recording/video/model";
import { Tooltip } from "@/components/ui/tooltip";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export type Overlays = { captions: boolean; faces: boolean; text: boolean };
export type VideoLayoutMode = "side" | "stacked" | "theatre";

/**
 * The video with its overlays (VR1): faces (blue, labelled boxes), text on screen (dashed gold boxes) and captions from
 * the transcript. Overlays redraw a few times a second from the player clock, not per frame.
 */
export function VideoStage({
  overlays,
  maxHeight = "62vh",
  className,
}: {
  overlays: Overlays;
  maxHeight?: string;
  className?: string;
}) {
  const { model, speakers } = useRec();
  const api = usePlayerApi();
  const { time, status, hasMedia } = usePlayerState();
  const color = useFaceColors();
  const aspect = model.media.width && model.media.height ? model.media.width / model.media.height : 16 / 9;
  const faces =
    overlays.faces && model.facesMode !== "off"
      ? model.faces.map((f, i) => ({ f, i, box: faceBoxAt(f, time) })).filter((x) => x.box)
      : [];
  const texts = overlays.text ? textAt(model.screenText, time) : [];
  const si = overlays.captions ? segmentAt(model.segments, time) : -1;
  const seg = si >= 0 && time <= model.segments[si].t1 + 800 ? model.segments[si] : null;
  const who = seg?.speaker ? speakers.get(seg.speaker)?.name : null;

  return (
    <div className={cn("grid place-items-center overflow-hidden rounded-[10px] bg-black", className)}>
      <div
        className="relative"
        style={{
          aspectRatio: String(aspect),
          width: `min(100%, calc(${maxHeight} * ${aspect}))`,
        }}
      >
        {hasMedia ? (
          <video
            ref={api.attach}
            src={model.audio ?? undefined}
            poster={model.poster ?? undefined}
            preload="metadata"
            playsInline
            onClick={() => api.toggle()}
            className="absolute inset-0 size-full cursor-pointer bg-black object-contain"
            aria-label={`Video: ${model.title}`}
          />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-[13px] text-white/60">
            The video file isn&apos;t available.
          </div>
        )}
        {status === "error" && (
          <div className="absolute inset-0 grid place-items-center bg-black/70 text-[13px] text-white">
            The video couldn&apos;t be loaded.
          </div>
        )}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          {texts.map((s) => (
            <div
              key={s.id}
              className="absolute rounded-[3px] border-2 border-dashed border-[var(--aladdin-gold)]"
              style={box(s.box!)}
            >
              <span className="absolute -bottom-[22px] -left-0.5 h-5 max-w-[260px] truncate whitespace-nowrap rounded-[0_4px_4px_4px] bg-[var(--aladdin-gold)] px-1.5 text-[11px] font-bold leading-5 text-black/85">
                Text · “{s.text}”
              </span>
            </div>
          ))}
          {faces.map(({ f, i, box: b }) => (
            <div key={f.id} className="absolute rounded-[4px] border-2" style={{ ...box(b!), borderColor: color(i) }}>
              <span
                className="absolute -left-0.5 -top-[22px] h-5 whitespace-nowrap rounded-[4px_4px_4px_0] px-1.5 text-[11px] font-bold leading-5 text-white"
                style={{ background: color(i) }}
              >
                {model.facesMode === "recognize" ? f.name : "Face"}
              </span>
            </div>
          ))}
          {seg && (
            <div className="absolute bottom-[5%] left-1/2 max-w-[80%] -translate-x-1/2 rounded-[6px] bg-black/75 px-3 py-1.5 text-center text-[clamp(12px,1.6vw,16px)] font-medium leading-[1.35] text-white">
              {who ? `${who}: ` : ""}
              {seg.text}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function box([x, y, w, h]: [number, number, number, number]) {
  return {
    left: `${x * 100}%`,
    top: `${y * 100}%`,
    width: `${w * 100}%`,
    height: `${h * 100}%`,
  };
}

/** "12:41.20 / 54:12", updated every frame while playing. */
export function FineTime() {
  const ref = useRef<HTMLSpanElement>(null);
  const { duration } = usePlayerState();
  usePlayerTick(
    useCallback((ms: number) => {
      if (ref.current) ref.current.textContent = fineTime(ms);
    }, []),
  );
  return (
    <span className="tabular whitespace-nowrap">
      <span ref={ref} className="text-[14px] font-semibold text-fg">
        0:00.00
      </span>{" "}
      <span className="text-[13px] text-fg-muted">/ {tc(duration)}</span>
    </span>
  );
}

/** Controls row under the video: play, fine time, frame step, speed, overlay toggles, layout, picture in picture. */
export function VideoControls({
  overlays,
  setOverlay,
  layout,
  setLayout,
  compact,
}: {
  overlays: Overlays;
  setOverlay: (k: keyof Overlays) => void;
  layout: VideoLayoutMode;
  setLayout: (l: VideoLayoutMode) => void;
  compact?: boolean;
}) {
  const { model } = useRec();
  const api = usePlayerApi();
  const { hasMedia } = usePlayerState();
  const [pipError, setPipError] = useState(false);
  const step = frameMs(model.media.fps);
  const faceOff = model.facesMode === "off";
  const toggles: {
    k: keyof Overlays;
    label: string;
    icon: typeof Captions;
    key: string;
    off?: string;
  }[] = [
    { k: "captions", label: "Captions", icon: Captions, key: "C" },
    {
      k: "faces",
      label: "Faces",
      icon: ScanFace,
      key: "F",
      off: faceOff ? "Face detection is off for this namespace" : undefined,
    },
    {
      k: "text",
      label: "Text",
      icon: ScanText,
      key: "T",
      off: !model.screenText.length ? "No text on screen was read in this video" : undefined,
    },
  ];
  const pip = async () => {
    const v = api.element() as HTMLVideoElement | null;
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await v?.requestPictureInPicture?.();
    } catch {
      setPipError(true);
    }
  };
  return (
    <div className="tabular flex flex-wrap items-center gap-2">
      <PlayButton size={36} noMediaReason="The video file isn't available" />
      <FineTime />
      <span
        className={cn(
          "h-[26px] items-center overflow-hidden rounded-[6px] border border-border",
          compact ? "hidden" : "inline-flex",
        )}
      >
        <Tooltip content="Previous frame ( , )">
          <button
            type="button"
            aria-label="Previous frame"
            disabled={!hasMedia}
            onClick={() => api.seekBy(-step)}
            className="grid h-full w-7 place-items-center hover:bg-surface-neutral disabled:opacity-40"
          >
            <StepBack className="size-3.5" />
          </button>
        </Tooltip>
        <span className="border-x border-border px-1.5 text-[11.5px] font-semibold leading-6">frame</span>
        <Tooltip content="Next frame ( . )">
          <button
            type="button"
            aria-label="Next frame"
            disabled={!hasMedia}
            onClick={() => api.seekBy(step)}
            className="grid h-full w-7 place-items-center hover:bg-surface-neutral disabled:opacity-40"
          >
            <StepForward className="size-3.5" />
          </button>
        </Tooltip>
      </span>
      {!compact && <SpeedMenu className="h-[26px]" />}
      <span className="flex-1" />
      {toggles.map((t) => {
        const on = overlays[t.k] && !t.off;
        const btn = (
          <button
            key={t.k}
            type="button"
            aria-pressed={on}
            aria-disabled={t.off ? true : undefined}
            aria-keyshortcuts={t.key}
            onClick={() => !t.off && setOverlay(t.k)}
            className={cn(
              "inline-flex h-7 items-center gap-[5px] rounded-pill border px-2.5 text-[12px] font-semibold",
              on
                ? "border-blue-border bg-blue-surface text-fg-accent"
                : "border-border bg-background text-fg-strong hover:bg-surface-neutral",
              t.off && "cursor-not-allowed opacity-50",
            )}
          >
            <t.icon className="size-[13px]" />
            {!compact && t.label}
            {compact && <span className="sr-only">{t.label}</span>}
          </button>
        );
        return (
          <Tooltip key={t.k} content={t.off ?? `${t.label} (${t.key})`}>
            {btn}
          </Tooltip>
        );
      })}
      {!compact && (
        <span role="radiogroup" aria-label="Layout" className="flex rounded-pill bg-surface-neutral p-0.5">
          {(
            [
              ["side", "Side by side", Columns2],
              ["stacked", "Stacked", Rows2],
              ["theatre", "Theatre", RectangleHorizontal],
            ] as const
          ).map(([v, label, Icon]) => (
            <Tooltip key={v} content={label}>
              <button
                type="button"
                role="radio"
                aria-checked={layout === v}
                aria-label={label}
                onClick={() => setLayout(v)}
                className={cn(
                  "grid h-[26px] w-[30px] place-items-center rounded-pill",
                  layout === v ? "bg-background shadow-1" : "text-fg-secondary hover:text-fg",
                )}
              >
                <Icon className="size-3.5" />
              </button>
            </Tooltip>
          ))}
        </span>
      )}
      {!compact && (
        <Tooltip content={pipError ? "Picture in picture isn't available in this browser" : "Picture in picture"}>
          <button
            type="button"
            aria-label="Picture in picture"
            disabled={!hasMedia}
            onClick={() => void pip()}
            className="grid size-[30px] place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral disabled:opacity-40"
          >
            <PictureInPicture2 className="size-4" />
          </button>
        </Tooltip>
      )}
    </div>
  );
}
