"use client";

import { Paperclip } from "lucide-react";
import { useCallback, useMemo } from "react";

import { usePlayerState } from "@/components/player/media";
import {
  PlayButton,
  SkipButton,
  SkipSilence,
  SpeedMenu,
  TimeReadout,
  VolumeControl,
} from "@/components/player/transport";
import { useMediaQuery } from "@/components/player/use-media-query";
import { Waveform, type WaveLane, type WaveMode, type WaveTick } from "@/components/player/waveform";
import { ChapterNow, ChaptersRail } from "@/components/recording/chapters";
import { useRec } from "@/components/recording/context";
import { RecordingHeader } from "@/components/recording/header";
import { loopSteps } from "@/components/recording/jobs";
import { ariaTimeText, chapterAt, segmentAt, transcribedFraction, type Segment } from "@/components/recording/model";
import { SidePanel } from "@/components/recording/side-panel";
import { Transcript } from "@/components/recording/transcript";
import { Button } from "@/components/ui/button";
import { StepLoop } from "@/components/ui/loop";
import { tc } from "@/lib/format";

const MAX_LANES = 6;

/** Waveform inputs shared by the desktop and phone players: lanes, mode, ticks and the slider's value text. */
export function useWave() {
  const { model, state, find, entity, speakers } = useRec();
  const { hasMedia } = usePlayerState();
  const processing = state.phase === "processing";
  const mode: WaveMode = processing ? "unsorted" : hasMedia && model.envelope ? "wave" : "timeline";

  // At most six lanes: the five who talk most, then "Others".
  const { lanes, segments } = useMemo(() => {
    if (mode === "unsorted")
      return {
        lanes: [{ key: "", name: "Unsorted", color: "var(--text-muted)" }] as WaveLane[],
        segments: model.segments.map((s) => ({ ...s, speaker: "" })),
      };
    const talk = new Map<string, number>();
    for (const s of model.segments) if (s.speaker) talk.set(s.speaker, (talk.get(s.speaker) ?? 0) + (s.t1 - s.t0));
    const ordered = model.speakers.filter((s) => talk.has(s.key));
    if (ordered.length <= MAX_LANES)
      return {
        lanes: ordered.map((s) => ({
          key: s.key,
          name: s.name,
          color: s.color,
        })),
        segments: model.segments,
      };
    const top = new Set(
      [...ordered]
        .sort((a, b) => (talk.get(b.key) ?? 0) - (talk.get(a.key) ?? 0))
        .slice(0, MAX_LANES - 1)
        .map((s) => s.key),
    );
    const segs: Segment[] = model.segments.map((s) =>
      s.speaker && !top.has(s.speaker) ? { ...s, speaker: "others" } : s,
    );
    return {
      lanes: [
        ...ordered.filter((s) => top.has(s.key)).map((s) => ({ key: s.key, name: s.name, color: s.color })),
        { key: "others", name: "Others", color: "var(--text-muted)" },
      ],
      segments: segs,
    };
  }, [mode, model.segments, model.speakers]);

  const ticks = useMemo<WaveTick[]>(() => {
    const out: WaveTick[] = [];
    const q = find.query.trim();
    const seen = new Set<number>();
    for (const h of find.hits) {
      if (seen.has(h.seg) || out.length > 150) continue;
      seen.add(h.seg);
      out.push({
        t: model.segments[h.seg]?.t0 ?? 0,
        kind: "hit",
        tip: `Find “${q}” at ${tc(model.segments[h.seg]?.t0 ?? 0)}`,
      });
    }
    if (entity.selected) {
      for (const s of entity.selected.segs.slice(0, 150))
        out.push({
          t: model.segments[s]?.t0 ?? 0,
          kind: "mention",
          tip: `Mention of ${entity.selected.name} at ${tc(model.segments[s]?.t0 ?? 0)}`,
        });
    }
    return out;
  }, [find.hits, find.query, entity.selected, model.segments]);

  const valueText = useCallback(
    (ms: number) => {
      const i = segmentAt(model.segments, ms);
      const spk = i >= 0 && model.segments[i].speaker ? speakers.get(model.segments[i].speaker as string)?.name : null;
      return ariaTimeText(ms, model.durationMs, spk, chapterAt(model.chapters, ms));
    },
    [model.segments, model.durationMs, model.chapters, speakers],
  );

  return {
    mode,
    lanes,
    segments,
    ticks,
    valueText,
    chapters: processing ? [] : model.chapters,
    progress: transcribedFraction(model.segments, model.durationMs),
  };
}

/** Desktop audio page (R1–R6, R8): header, then chapters · player over transcript · side panel. */
export function AudioLayout() {
  const wide = useMediaQuery("(min-width: 1280px)");
  return (
    <div className="flex h-[calc(100dvh-4rem)] min-h-[600px] flex-col overflow-hidden">
      <RecordingHeader />
      <div
        className="grid min-h-0 flex-1"
        style={{
          gridTemplateColumns: wide
            ? "200px minmax(0,1fr) clamp(360px,36%,488px)"
            : "minmax(0,1fr) clamp(340px,40%,440px)",
        }}
      >
        {wide && <ChaptersRail className="flex" />}
        <section aria-label="Player and transcript" className="flex min-h-0 min-w-0 flex-col">
          <PlayerPanel chapterMenu={!wide} />
          <Transcript />
        </section>
        <SidePanel />
      </div>
    </div>
  );
}

function PlayerPanel({ chapterMenu }: { chapterMenu: boolean }) {
  const { model, state, transcriptOnly } = useRec();
  const wave = useWave();
  const { hasMedia, status } = usePlayerState();
  return (
    <div className="flex shrink-0 flex-col gap-2.5 border-b border-border px-6 pb-3 pt-3.5">
      {state.phase === "processing" && state.job && (
        <div className="pb-2 pt-0.5">
          <StepLoop steps={loopSteps(state.job)} label="Processing steps" />
        </div>
      )}
      <div className="tabular flex min-w-0 items-center gap-2.5">
        <PlayButton />
        <SkipButton dir={-1} />
        <SkipButton dir={1} />
        <TimeReadout />
        <ChapterNow menu={chapterMenu} />
        <span className="flex-1" />
        {transcriptOnly ? (
          <Button
            variant="secondary"
            size="sm"
            icon={<Paperclip />}
            disabled
            disabledReason="Attaching audio to an imported transcript isn't available yet"
          >
            Attach audio
          </Button>
        ) : (
          <>
            {status === "error" && (
              <span className="text-[12.5px] text-red-dark">The audio couldn&apos;t be loaded</span>
            )}
            <SpeedMenu />
            <SkipSilence />
            <VolumeControl />
          </>
        )}
      </div>
      <Waveform
        mode={wave.mode}
        lanes={wave.lanes}
        segments={wave.segments}
        envelope={model.envelope}
        durationMs={model.durationMs}
        chapters={wave.chapters}
        ticks={wave.ticks}
        valueText={wave.valueText}
        progress={wave.progress}
        showPlayhead
        className={!hasMedia ? "opacity-95" : undefined}
      />
    </div>
  );
}
