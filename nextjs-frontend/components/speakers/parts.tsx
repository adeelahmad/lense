"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { Loader2, Pause, Play } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Recordings, Speakers } from "@/app/openapi-client";
import type { Speaker } from "@/app/openapi-client/types.gen";
import { playerSegments, playerSpeakers, useRecordingIndex } from "@/components/search/data";
import { hasMedia, recordingHref } from "@/components/search/links";
import type { InlinePlayer } from "@/components/search/player";
import { isUnnamed, speakerInitials, speakerTone } from "@/components/speakers/format";
import { EMOJI } from "@/components/ui/badge";
import { data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A speaker's disc: initials in their colour; unnamed voices are a dashed ring with their number. */
export function SpeakerAvatar({
  s,
  size = 26,
}: {
  s: Pick<Speaker, "id" | "name" | "label" | "display">;
  size?: number;
}) {
  const unnamed = isUnnamed(s);
  const color = speakerTone(s.id);
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-full font-bold"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.38),
        background: unnamed ? "transparent" : color,
        border: unnamed ? `1.5px dashed ${color}` : undefined,
        color: unnamed ? color : "var(--background)",
      }}
    >
      {speakerInitials(s.display)}
    </span>
  );
}

export function useNamespaceSpeakers(ns: string | null | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["speakers", ns],
    queryFn: () => data(Speakers.listSpeakers({ client, query: { ns: ns as string } })),
    enabled: Boolean(ns),
    staleTime: 30_000,
  });
}

export function useSpeakerRecordings(sid: number | null | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["speaker-recordings", sid],
    queryFn: () =>
      data(
        Speakers.listSpeakerRecordings({
          client,
          path: { sid: sid as number },
        }),
      ),
    enabled: sid != null,
    staleTime: 60_000,
  });
}

export type Clip = {
  key: string;
  recordingId: number;
  title: string;
  t0: number;
  t1: number;
  text: string;
  emotion?: string | null;
};

/**
 * Sample lines for a speaker: from their busiest recordings, their longest turns. Reads each recording's player data
 * (at most `recordings` of them).
 */
export function useSpeakerClips(sid: number | null | undefined, { recordings = 3, perRecording = 1, max = 3 } = {}) {
  const client = useApiClient();
  const recs = useSpeakerRecordings(sid);
  const top = useMemo(
    () => [...(recs.data ?? [])].sort((a, b) => (b.talk_ms ?? 0) - (a.talk_ms ?? 0)).slice(0, recordings),
    [recs.data, recordings],
  );
  const players = useQueries({
    queries: top.map((r) => ({
      queryKey: ["player", r.id],
      queryFn: () => data(Recordings.getPlayer({ client, path: { rid: r.id } })),
      staleTime: 5 * 60_000,
    })),
  });
  const clips = useMemo<Clip[]>(() => {
    const out: Clip[] = [];
    top.forEach((r, i) => {
      const p = players[i]?.data;
      if (!p) return;
      const keys = new Set(
        playerSpeakers(p)
          .filter((s) => s.id === sid)
          .map((s) => s.key),
      );
      const mine = playerSegments(p)
        .filter((s) => s.s && keys.has(s.s) && s.text.trim())
        .sort((a, b) => b.t1 - b.t0 - (a.t1 - a.t0))
        .slice(0, perRecording);
      for (const s of mine)
        out.push({
          key: `${r.id}-${s.t0}`,
          recordingId: r.id,
          title: r.title ?? `Recording ${r.id}`,
          t0: s.t0,
          t1: s.t1,
          text: s.text,
          emotion: s.e,
        });
    });
    return out.slice(0, max);
  }, [top, players.map((p) => p.dataUpdatedAt).join(","), sid, perRecording, max]);
  return {
    clips,
    isLoading: recs.isLoading || players.some((p) => p.isLoading),
    isError: recs.isError || players.some((p) => p.isError),
    recordings: recs,
  };
}

/** One clip: play in place (when there is audio), the line, where it's from. */
export function ClipRow({
  clip,
  player,
  showEmotion,
  compact,
}: {
  clip: Clip;
  player: InlinePlayer;
  showEmotion?: boolean;
  compact?: boolean;
}) {
  const index = useRecordingIndex();
  const playing = player.isPlaying(clip.key);
  const none = hasMedia(index.byId.get(clip.recordingId)) === false || player.noAudio.has(clip.recordingId);
  const where = `${clip.title.split(/\s+[—–]\s+/)[0]} · ${tc(clip.t0)}`;
  return (
    <div
      className={cn(
        "grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-[10px] bg-surface py-2 pl-2.5 pr-3",
        compact && "py-1.5",
      )}
    >
      <button
        type="button"
        aria-label={none ? "No audio — transcript only" : `${playing ? "Pause" : "Play"} “${clip.text.slice(0, 60)}”`}
        aria-disabled={none || undefined}
        onClick={() =>
          !none &&
          player.play({
            key: clip.key,
            recordingId: clip.recordingId,
            t0: clip.t0,
            title: clip.title,
          })
        }
        className={cn(
          "grid size-8 place-items-center rounded-full border border-border bg-background text-fg [&_svg]:size-[13px]",
          none ? "cursor-not-allowed opacity-40" : "hover:border-blue hover:text-blue",
          playing && "border-blue bg-blue text-white",
        )}
      >
        {player.loading === clip.key ? (
          <Loader2 className="animate-spin" />
        ) : playing ? (
          <Pause />
        ) : (
          <Play className="translate-x-px" />
        )}
      </button>
      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className={cn("font-serif text-[14px] leading-[1.35] text-fg", compact ? "truncate" : "line-clamp-2")}>
          {clip.text}
        </span>
        <Link
          href={recordingHref(clip.recordingId, clip.t0)}
          className="w-fit text-[11.5px] text-fg-muted hover:text-fg-accent hover:underline"
        >
          {where}
          {showEmotion && clip.emotion && clip.emotion !== "Unknown"
            ? ` · ${EMOJI[clip.emotion] ?? ""} ${clip.emotion}`
            : ""}
        </Link>
      </span>
      <span className="tabular text-[12px] font-medium text-fg-secondary">{tc(clip.t1 - clip.t0)}</span>
    </div>
  );
}
