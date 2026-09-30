"use client";

import { Pause, Play, RotateCcw, RotateCw, Volume1, Volume2, VolumeX } from "lucide-react";

import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { Switch } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger, Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { Tooltip } from "@/components/ui/tooltip";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2];

export function rateLabel(r: number): string {
  return `${Number.isInteger(r) ? r : r.toFixed(2).replace(/0$/, "")}×`;
}

/** Play/pause: blue when there's audio; grey with a tooltip saying why when there isn't. */
export function PlayButton({ size = 38, noMediaReason }: { size?: number; noMediaReason?: string }) {
  const api = usePlayerApi();
  const { hasMedia, playing, status } = usePlayerState();
  const off = !hasMedia || status === "error";
  const reason = !hasMedia ? (noMediaReason ?? "No audio — this recording is a transcript only") : status === "error" ? "The audio couldn't be loaded" : undefined;
  const btn = (
    <button
      type="button"
      aria-label={off ? `Play (unavailable: ${reason})` : playing ? "Pause (Space)" : "Play (Space)"}
      aria-disabled={off || undefined}
      onClick={() => !off && api.toggle()}
      className={cn("grid shrink-0 place-items-center rounded-full text-white transition-colors duration-fast", off ? "cursor-not-allowed bg-border text-fg-muted" : "bg-blue hover:bg-blue-dark")}
      style={{ width: size, height: size }}
    >
      {playing ? <Pause className="size-[45%]" fill="currentColor" strokeWidth={0} /> : <Play className="ml-[6%] size-[45%]" fill="currentColor" strokeWidth={0} />}
    </button>
  );
  return off ? <Tooltip content={reason}>{btn}</Tooltip> : <Tooltip content={playing ? "Pause (Space)" : "Play (Space)"}>{btn}</Tooltip>;
}

/** ±10 s buttons (J / L). With no audio they still move the reading position. */
export function SkipButton({ dir, size = 30, iconSize = 17 }: { dir: -1 | 1; size?: number; iconSize?: number }) {
  const api = usePlayerApi();
  const label = dir < 0 ? "Back 10 seconds (J)" : "Forward 10 seconds (L)";
  const Icon = dir < 0 ? RotateCcw : RotateCw;
  return (
    <Tooltip content={label}>
      <button
        type="button"
        aria-label={label}
        onClick={() => api.seekBy(dir * 10_000, { manual: true })}
        className="grid shrink-0 place-items-center rounded-full text-fg-strong hover:bg-surface-neutral"
        style={{ width: size, height: size }}
      >
        <Icon style={{ width: iconSize, height: iconSize }} />
      </button>
    </Tooltip>
  );
}

/** "14:32 / 47:18". */
export function TimeReadout({ className }: { className?: string }) {
  const { time, duration } = usePlayerState();
  return (
    <span className={cn("tabular whitespace-nowrap text-[14px] font-semibold leading-none text-fg", className)}>
      {tc(time)} <span className="text-[13px] font-normal text-fg-muted">/ {tc(duration)}</span>
    </span>
  );
}

export function SpeedMenu({ className }: { className?: string }) {
  const api = usePlayerApi();
  const { rate } = usePlayerState();
  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          type="button"
          aria-label={`Playback speed, ${rateLabel(rate)}`}
          className={cn("tabular h-7 rounded-pill border border-border px-[9px] text-[12px] font-semibold leading-7 text-fg-strong hover:bg-surface-neutral", className)}
        >
          {rateLabel(rate)}
        </button>
      </MenuTrigger>
      <MenuContent className="min-w-[140px]">
        <MenuLabel>Speed</MenuLabel>
        {RATES.map((r) => (
          <MenuItem key={r} onSelect={() => api.setRate(r)} shortcut={r === rate ? "✓" : undefined}>
            {rateLabel(r)}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}

export function SkipSilence() {
  const api = usePlayerApi();
  const { skipSilence } = usePlayerState();
  return (
    <Tooltip content="Jump over pauses longer than 1.5 s between lines">
      <span>
        <Switch checked={skipSilence} onCheckedChange={api.setSkipSilence} label={<span className="whitespace-nowrap text-[13px] text-fg-secondary">Skip silence</span>} />
      </span>
    </Tooltip>
  );
}

export function VolumeControl() {
  const api = usePlayerApi();
  const { volume, muted } = usePlayerState();
  const Icon = muted || volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" aria-label={`Volume, ${muted ? "muted" : `${Math.round(volume * 100)}%`}`} className="grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral">
          <Icon className="size-[17px]" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="flex w-[200px] items-center gap-3 p-3">
        <button type="button" aria-label={muted ? "Unmute" : "Mute"} onClick={() => api.setMuted(!muted)} className="grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral">
          <Icon className="size-4" />
        </button>
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round((muted ? 0 : volume) * 100)}
          aria-label="Volume"
          onChange={(e) => api.setVolume(Number(e.target.value) / 100)}
          className="h-1 flex-1 accent-[var(--aladdin-blue)]"
        />
      </PopoverContent>
    </Popover>
  );
}
