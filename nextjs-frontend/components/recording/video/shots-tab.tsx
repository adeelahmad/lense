"use client";

import { Clapperboard, Eye } from "lucide-react";

import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { descriptionOf } from "@/components/recording/model";
import { shotAt } from "@/components/recording/video/model";
import { EmptyState } from "@/components/ui/states";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Shots: a grid of keyframes, or, once they're described, a list of each keyframe beside what it shows; the current
 * shot is outlined; click to jump. Shift+←/→ steps between shots.
 */
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
  const described = model.descriptions.some((d) => !d.paged);
  const by = model.descriptions.find((d) => !d.paged)?.model;
  return (
    <div className="flex flex-col gap-2.5">
      {described && (
        <p className="flex items-center gap-1.5 text-[12px] text-fg-muted">
          <Eye className="size-3.5" aria-hidden />
          What each shot shows, as {by ?? "a model that can see images"} described it
        </p>
      )}
      <ol
        className={cn(
          "m-0 list-none p-0",
          described ? "flex flex-col gap-2" : "grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3",
        )}
        aria-label="Shots"
      >
        {model.shots.map((s, i) => {
          const shows = descriptionOf(model.descriptions, s.idx, false);
          return (
            <li key={s.idx}>
              <button
                type="button"
                aria-current={i === cur ? "true" : undefined}
                onClick={() => api.seek(s.t0, { manual: true })}
                className={cn(
                  "w-full gap-1.5 rounded-md border p-1.5 text-left hover:bg-surface-neutral",
                  described
                    ? "grid grid-cols-[112px_minmax(0,1fr)] items-start gap-x-3 sm:grid-cols-[160px_minmax(0,1fr)]"
                    : "flex flex-col",
                  i === cur ? "border-blue bg-blue-surface" : "border-border",
                )}
              >
                <span className="block aspect-video overflow-hidden rounded-[6px] bg-black">
                  {s.frame && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={s.frame} alt={shows?.text ?? ""} loading="lazy" className="size-full object-cover" />
                  )}
                </span>
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="flex items-baseline justify-between gap-2 px-0.5">
                    <span className="text-[12.5px] font-bold text-fg">Shot {i + 1}</span>
                    <span className="tabular text-[11.5px] text-fg-muted">
                      {tc(s.t0)} · {tc(Math.max(0, s.t1 - s.t0))}
                    </span>
                  </span>
                  {shows && (
                    <span className="px-0.5 text-[12.5px] leading-snug text-fg-secondary" aria-hidden>
                      {shows.text}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
