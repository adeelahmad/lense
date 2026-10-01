"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Pause, Play, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Resources } from "@/app/openapi-client";
import { recordingHref } from "@/components/search/links";
import { IconButton } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";

export type Moment = {
  key: string;
  recordingId: number;
  t0: number;
  title?: string | null;
  speaker?: string | null;
};

/**
 * One shared inline player for a list of moments (search hits, mentions, clips). Play fetches the recording's
 * signed audio link on demand; recordings without audio say so instead of failing silently.
 */
export function useInlinePlayer() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const audio = useRef<HTMLAudioElement | null>(null);
  const [current, setCurrent] = useState<(Moment & { src: string }) | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [loading, setLoading] = useState<string | null>(null);
  const [noAudio, setNoAudio] = useState<Set<number>>(() => new Set());

  useEffect(() => {
    const a = audio.current;
    if (!a || !current) return;
    a.src = `${current.src}#t=${(current.t0 / 1000).toFixed(2)}`;
    a.play().catch(() => setPlaying(false));
  }, [current]);

  const play = useCallback(
    async (m: Moment) => {
      if (current?.key === m.key && audio.current) {
        if (audio.current.paused) audio.current.play().catch(() => undefined);
        else audio.current.pause();
        return;
      }
      setLoading(m.key);
      try {
        const p = await qc.fetchQuery({
          queryKey: ["player", m.recordingId],
          queryFn: () => data(Resources.getPlayer({ client, path: { rid: m.recordingId } })),
          staleTime: 5 * 60_000,
        });
        if (!p.audio) {
          setNoAudio((s) => new Set(s).add(m.recordingId));
          toast({
            title: "No audio to play",
            body: "This recording is a transcript only. Open it to read the moment.",
            tone: "gate",
          });
          return;
        }
        setCurrent({ ...m, src: p.audio });
      } catch (e) {
        toast({
          title: "Couldn’t load the audio",
          body: e instanceof Error ? e.message : undefined,
          tone: "red",
        });
      } finally {
        setLoading(null);
      }
    },
    [client, current, qc, toast],
  );

  const stop = useCallback(() => {
    audio.current?.pause();
    setCurrent(null);
    setPlaying(false);
  }, []);

  const bind = {
    ref: audio,
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onEnded: () => setPlaying(false),
    onTimeUpdate: (e: React.SyntheticEvent<HTMLAudioElement>) => setTime(e.currentTarget.currentTime),
  };
  return {
    current,
    playing,
    time,
    loading,
    noAudio,
    play,
    stop,
    bind,
    isPlaying: (key: string) => playing && current?.key === key,
  };
}

export type InlinePlayer = ReturnType<typeof useInlinePlayer>;

/** The bar under a list while a moment plays: pause, where it is, open the recording there, close. */
export function InlinePlayerBar({ player, className }: { player: InlinePlayer; className?: string }) {
  const { current, playing, time, bind, stop } = player;
  return (
    <>
      <audio {...bind} preload="none" className="hidden" />
      {current && (
        <div role="region" aria-label="Player" className={className}>
          <div className="flex items-center gap-3 rounded-md border border-border bg-background px-3 py-2 shadow-2">
            <IconButton
              label={playing ? "Pause" : "Play"}
              onClick={() => (playing ? bind.ref.current?.pause() : bind.ref.current?.play())}
              className="bg-blue text-white hover:bg-blue-dark"
            >
              {playing ? <Pause /> : <Play />}
            </IconButton>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13.5px] font-bold text-fg">
                {current.title ?? `Recording ${current.recordingId}`}
              </div>
              <div className="tabular text-[12px] text-fg-secondary">
                {tc(time * 1000)}
                {current.speaker ? ` · from ${current.speaker} at ${tc(current.t0)}` : ` · from ${tc(current.t0)}`}
              </div>
            </div>
            <Link
              href={recordingHref(current.recordingId, Math.round(time * 1000))}
              className="inline-flex items-center gap-1 text-[13px] font-bold text-fg-accent hover:underline"
            >
              Open here <ExternalLink className="size-3.5" aria-hidden />
            </Link>
            <IconButton label="Close player" onClick={stop}>
              <X />
            </IconButton>
          </div>
        </div>
      )}
    </>
  );
}
