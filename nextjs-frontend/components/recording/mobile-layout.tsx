"use client";

import * as D from "@radix-ui/react-dialog";
import { ChevronLeft, PanelBottomOpen } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { accessLabel } from "@/components/access/model";
import { usePlayerState } from "@/components/player/media";
import { PlayButton, SkipButton, SpeedMenu } from "@/components/player/transport";
import { Waveform } from "@/components/player/waveform";
import { useWave } from "@/components/recording/audio-layout";
import { ChapterNow } from "@/components/recording/chapters";
import { useRec, type PanelTab } from "@/components/recording/context";
import { Banners, HeaderActions } from "@/components/recording/header";
import { AUDIO_TABS, MORE_TABS, PanelBody, PanelScroll, PanelTabs } from "@/components/recording/side-panel";
import { Transcript } from "@/components/recording/transcript";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_WORD: Record<string, string> = {
  analyzed: "Ready",
  transcribed: "Transcribed",
  diarized: "Diarized",
  new: "New",
  error: "Error",
};
const SHEET_TABS = AUDIO_TABS.filter((t) => t.value !== "history");
const SHEET_MORE = [AUDIO_TABS.find((t) => t.value === "history")!, ...MORE_TABS];

/**
 * Phone and narrow screens (R7): a compact header, the transcript, the player docked at the bottom, and the panels in
 * a bottom sheet (half height; the handle expands it). History sits in the ⋯ menu and the sheet's More menu.
 */
export function MobileLayout() {
  const r = useRec();
  const { model, rec, role, state, tab, setTab } = r;
  const [sheet, setSheet] = useState(false);
  const status = (rec.status ?? "").toLowerCase();
  const word =
    state.phase === "processing" || state.phase === "analyzing" ? "Processing" : (STATUS_WORD[status] ?? status);
  const openTab = (t: PanelTab) => {
    setTab(t);
    setSheet(true);
  };
  // "Ask in chat" and the ⋯ menu's History switch the tab; open the sheet to show it.
  const [lastTab, setLastTab] = useState(tab);
  if (tab !== lastTab) {
    setLastTab(tab);
    if (tab === "chat" || tab === "history" || tab === "details") setSheet(true);
  }

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col overflow-hidden">
      <div className="flex items-center gap-1.5 border-b border-border px-2 pb-2 pt-1">
        <Link
          href="/library"
          aria-label="Back to the Library"
          className="grid size-11 shrink-0 place-items-center rounded-full text-fg hover:bg-surface-neutral"
        >
          <ChevronLeft className="size-[22px]" />
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[15px] font-bold leading-tight text-fg">{model.title}</h1>
          <p className="tabular truncate text-[12px] leading-snug text-fg-muted">
            {[
              r.ns,
              role ? role[0].toUpperCase() + role.slice(1) : null,
              tc(model.durationMs),
              word,
              r.transcriptOnly ? "Transcript only" : null,
              rec.access && rec.access !== "private" ? accessLabel(rec.access) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <HeaderActions compact />
      </div>
      {(state.phase === "analyzing" || state.phase === "failed") && <Banners className="mx-3 mt-2" />}
      <Transcript compact className="min-h-0 flex-1" />
      <DockedPlayer
        onPanels={() =>
          openTab(SHEET_TABS.some((t) => t.value === tab) || SHEET_MORE.some((t) => t.value === tab) ? tab : "summary")
        }
      />
      <D.Root open={sheet} onOpenChange={setSheet}>
        <D.Portal>
          <D.Overlay className="fixed inset-0 z-[100] bg-[var(--scrim)] animate-fade-in" />
          <Sheet tab={tab} setTab={setTab} />
        </D.Portal>
      </D.Root>
    </div>
  );
}

function Sheet({ tab, setTab }: { tab: PanelTab; setTab: (t: PanelTab) => void }) {
  const [full, setFull] = useState(false);
  const current = [...SHEET_TABS, ...SHEET_MORE].some((t) => t.value === tab) ? tab : "summary";
  return (
    <D.Content
      aria-describedby={undefined}
      className={cn(
        "fixed inset-x-0 bottom-0 z-[101] flex flex-col rounded-t-[20px] bg-background shadow-3 outline-none animate-fade-in",
        full ? "h-[92dvh]" : "h-[min(560px,70dvh)]",
      )}
    >
      <D.Title className="sr-only">Recording panels</D.Title>
      <button
        type="button"
        onClick={() => setFull((f) => !f)}
        aria-label={full ? "Half height" : "Full height"}
        className="mx-auto mb-1 mt-2 flex h-4 w-16 items-center justify-center"
      >
        <span className="h-1 w-9 rounded-[2px] bg-border" />
      </button>
      <PanelTabs
        tabs={SHEET_TABS}
        more={SHEET_MORE}
        value={current}
        onChange={setTab}
        idBase="sheet"
        className="px-3"
      />
      <PanelScroll id="sheet" tab={current} className="px-[18px]">
        <PanelBody tab={current} />
      </PanelScroll>
    </D.Content>
  );
}

function DockedPlayer({ onPanels }: { onPanels: () => void }) {
  const { model } = useRec();
  const wave = useWave();
  const { time, duration } = usePlayerState();
  return (
    <div className="flex shrink-0 flex-col gap-2 border-t border-border bg-background px-4 pb-2 pt-2.5">
      <Waveform
        compact
        mode={wave.mode}
        lanes={wave.lanes.slice(0, 3)}
        segments={wave.segments}
        envelope={model.envelope}
        durationMs={model.durationMs}
        chapters={[]}
        ticks={[]}
        valueText={wave.valueText}
        progress={wave.progress}
        bars={70}
      />
      <div className="tabular flex items-center gap-1.5">
        <span className="text-[12px] font-semibold leading-none">{tc(time)}</span>
        <span className="flex min-w-0 flex-1 justify-center">
          <ChapterNow className="!ml-0 text-center text-[12px]" />
        </span>
        <span className="text-[12px] leading-none text-fg-muted">{tc(duration)}</span>
      </div>
      <div className="flex items-center justify-between">
        <SpeedMenu className="h-11 w-11 rounded-full border-0 px-0 text-[13px] font-bold" />
        <SkipButton dir={-1} size={44} iconSize={22} />
        <PlayButton size={56} />
        <SkipButton dir={1} size={44} iconSize={22} />
        <button
          type="button"
          onClick={onPanels}
          aria-label="Open panels"
          className="grid size-11 place-items-center rounded-full text-fg hover:bg-surface-neutral"
        >
          <PanelBottomOpen className="size-[22px]" />
        </button>
      </div>
    </div>
  );
}
