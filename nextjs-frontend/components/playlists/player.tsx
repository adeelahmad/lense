"use client";

import { ExternalLink, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Clip } from "@/app/openapi-client/types.gen";
import { clipTitle, firstPlayable, nextPlayable, reachedEnd } from "@/components/playlists/model";
import { recordingHref } from "@/components/search/links";
import { IconButton } from "@/components/ui/button";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Plays a playlist's clips back to back on one media element: each clip from t0 to t1 of its resource's signed link
 * (as the recording player gets it), then the next with media; clips of text are skipped. Clips of one resource in a
 * row play without loading it again.
 */
export function usePlaylistPlayer(items: Clip[]) {
  const el = useRef<HTMLVideoElement | null>(null);
  const loaded = useRef<string | null>(null);
  const list = useRef(items);
  const at = useRef<number | null>(null);
  const [index, setIndexState] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  useEffect(() => {
    list.current = items;
  }, [items]);
  const setIndex = useCallback((i: number | null) => {
    at.current = i;
    setIndexState(i);
  }, []);

  const start = useCallback(
    (i: number | null) => {
      const a = el.current;
      const c = i == null ? null : list.current[i];
      if (!a || !c?.audio) {
        a?.pause();
        return;
      }
      setIndex(i);
      setTime(c.t0);
      const go = () => {
        a.currentTime = c.t0 / 1000;
        a.play().catch(() => setPlaying(false));
      };
      if (loaded.current !== c.audio) {
        loaded.current = c.audio;
        a.src = c.audio;
        a.addEventListener("loadedmetadata", go, { once: true });
        a.load();
      } else go();
    },
    [setIndex],
  );

  const playAll = useCallback(() => start(firstPlayable(list.current)), [start]);
  const next = useCallback(() => start(nextPlayable(list.current, at.current ?? -1)), [start]);
  const previous = useCallback(() => {
    const cur = at.current ?? 0;
    for (let i = cur - 1; i >= 0; i--) if (list.current[i]?.audio) return start(i);
    start(cur);
  }, [start]);
  const toggle = useCallback(() => {
    const a = el.current;
    if (!a) return;
    if (at.current == null) return playAll();
    if (a.paused) a.play().catch(() => setPlaying(false));
    else a.pause();
  }, [playAll]);

  const bind = {
    ref: el,
    playsInline: true,
    preload: "none" as const,
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onEnded: () => next(),
    onTimeUpdate: (e: React.SyntheticEvent<HTMLVideoElement>) => {
      const ms = e.currentTarget.currentTime * 1000;
      setTime(ms);
      const c = at.current == null ? null : list.current[at.current];
      if (c && reachedEnd(c, ms)) {
        const n = nextPlayable(list.current, at.current ?? -1);
        if (n == null) {
          e.currentTarget.pause();
          setIndex(null);
        } else start(n);
      }
    },
  };
  const current = index == null ? null : (items[index] ?? null);
  return { index, current, playing, time, start, playAll, next, previous, toggle, setIndex, bind };
}

export type PlaylistPlayer = ReturnType<typeof usePlaylistPlayer>;

/** The player: the video when the clip is of one, and the controls: back, play or pause, next, the clip and where it is. */
export function PlaylistPlayerBar({ player, count }: { player: PlaylistPlayer; count: number }) {
  const { current, index, playing, time, toggle, next, previous, bind } = player;
  const video = current?.media === "video";
  return (
    <div className="flex flex-col gap-2">
      <video
        {...bind}
        aria-label={current ? clipTitle(current) : "Player"}
        className={cn("max-h-[50vh] w-full rounded-md bg-black", !video && "hidden")}
      />
      <div
        role="region"
        aria-label="Player"
        className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2 shadow-2"
      >
        <IconButton label="Previous clip" onClick={previous} disabled={index == null}>
          <SkipBack />
        </IconButton>
        <IconButton
          label={playing ? "Pause" : index == null ? "Play all" : "Play"}
          onClick={toggle}
          disabled={!count}
          className="bg-blue text-white hover:bg-blue-dark"
        >
          {playing ? <Pause /> : <Play />}
        </IconButton>
        <IconButton label="Next clip" onClick={next} disabled={index == null}>
          <SkipForward />
        </IconButton>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-bold text-fg">
            {current ? clipTitle(current) : count ? "Play all, back to back" : "No clips to play"}
          </div>
          <div className="tabular text-[12px] text-fg-secondary">
            {current
              ? `${index! + 1} of ${count} · ${tc(Math.max(0, time - current.t0))} of ${tc(current.t1 - current.t0)}`
              : `${count === 1 ? "1 clip" : `${count} clips`}`}
          </div>
        </div>
        {current && (
          <Link
            href={recordingHref(current.recording, Math.round(time))}
            className="inline-flex items-center gap-1 text-[13px] font-bold text-fg-accent hover:underline"
          >
            Open here <ExternalLink className="size-3.5" aria-hidden />
          </Link>
        )}
      </div>
    </div>
  );
}
