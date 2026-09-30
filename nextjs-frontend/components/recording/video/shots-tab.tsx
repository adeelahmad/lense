"use client";

import { Clapperboard } from "lucide-react";

import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { shotAt } from "@/components/recording/video/model";
import { EmptyState } from "@/components/ui/states";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Shots: a grid of keyframes; the current shot is outlined; click to jump. Shift+←/→ steps between shots. */
export function ShotsTab() {
  const { model } = useRec();
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const cur = shotAt(model.shots, time);
  if (!model.shots.length)
    return (
      <EmptyState icon={<Clapperboard />} title="No shots yet" className="py-10">
        The Shots step finds scene cuts and saves a keyframe for each shot.
      </EmptyState>
    );
  return (
    <ol className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3 p-0" aria-label="Shots">
      {model.shots.map((s, i) => (
        <li key={s.idx}>
          <button
            type="button"
            aria-current={i === cur ? "true" : undefined}
            onClick={() => api.seek(s.t0, { manual: true })}
            className={cn(
              "flex w-full flex-col gap-1.5 rounded-md border p-1.5 text-left hover:bg-surface-neutral",
              i === cur ? "border-blue bg-blue-surface" : "border-border",
            )}
          >
            <span className="block aspect-video overflow-hidden rounded-[6px] bg-black">
              {s.frame && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={s.frame} alt="" loading="lazy" className="size-full object-cover" />
              )}
            </span>
            <span className="flex items-baseline justify-between gap-2 px-0.5">
              <span className="text-[12.5px] font-bold text-fg">Shot {i + 1}</span>
              <span className="tabular text-[11.5px] text-fg-muted">
                {tc(s.t0)} · {tc(Math.max(0, s.t1 - s.t0))}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}
