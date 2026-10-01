"use client";

import { ChevronLeft, Video as VideoIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { homeText } from "@/components/library/collections-model";
import type { PlayerAction } from "@/components/player/keys";
import { usePlayerApi } from "@/components/player/media";
import { useRec, type PanelTab } from "@/components/recording/context";
import { Banners, HeaderActions } from "@/components/recording/header";
import { sourceLabel } from "@/components/recording/labels";
import { useNotes, useVisualNotes } from "@/components/recording/hooks";
import { currentStep, isActive, loopSteps, type JobInfo } from "@/components/recording/jobs";
import { MORE_TABS, PanelBody, PanelScroll, PanelTabs, type TabDef } from "@/components/recording/side-panel";
import { Transcript } from "@/components/recording/transcript";
import { adjacentShot, frameMs } from "@/components/recording/video/model";
import { ObjectsTab } from "@/components/recording/objects-tab";
import { PeopleTab } from "@/components/recording/video/people-tab";
import { ScreenTextTab } from "@/components/recording/video/screen-text-tab";
import { ShotsTab } from "@/components/recording/video/shots-tab";
import { VideoControls, VideoStage, type Overlays, type VideoLayoutMode } from "@/components/recording/video/stage";
import { VideoTimeline } from "@/components/recording/video/timeline";
import { StatusChip } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/states";
import { shortDate, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export type VideoCommand = Extract<PlayerAction, { type: "frame" | "shot" | "overlay" | "find" }>;

const LAYOUT_KEY = "lens.video.layout";

/**
 * The video recording page (VR1–VR3): the player first with face, text and caption overlays; a zoomable timeline with
 * lanes for shots, voices, people and text on screen; tabs for shots, text on screen and people; the transcript from
 * the soundtrack beside it (side by side), under it (stacked) or as captions (theatre).
 */
export function VideoLayout({
  compact,
  onCommand,
}: {
  compact: boolean;
  onCommand: (fn: (c: VideoCommand) => void) => void;
}) {
  const r = useRec();
  const { model, rec, state, jobs, tab, setTab } = r;
  const api = usePlayerApi();
  const [overlays, setOverlays] = useState<Overlays>({
    captions: true,
    faces: true,
    text: true,
  });
  const [layout, setLayoutState] = useState<VideoLayoutMode>("side");
  const [object, setObject] = useState<string | null>(null);
  const notes = useVisualNotes(jobs);
  const notesCount = useNotes(r.id).data?.length ?? 0;
  useEffect(() => {
    try {
      const v = localStorage.getItem(LAYOUT_KEY);
      if (v === "side" || v === "stacked" || v === "theatre") setLayoutState(v);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const setLayout = (l: VideoLayoutMode) => {
    setLayoutState(l);
    try {
      localStorage.setItem(LAYOUT_KEY, l);
    } catch {
      /* storage unavailable */
    }
  };
  const mode: VideoLayoutMode = compact ? "stacked" : layout;
  const toggle = (k: keyof Overlays) => setOverlays((o) => ({ ...o, [k]: !o[k] }));

  // , . frame steps · Shift+←/→ shots · C F T overlays (routed here by the page's key handler).
  useEffect(() => {
    onCommand((c) => {
      if (c.type === "find") {
        if (mode !== "side") setTab("transcript");
      } else if (c.type === "frame") api.seekBy(c.dir * frameMs(model.media.fps));
      else if (c.type === "shot") {
        const t = adjacentShot(model.shots, api.now(), c.dir);
        if (t != null) api.seek(t, { manual: true });
      } else toggle(c.which);
    });
  }, [api, model.media.fps, model.shots, onCommand, mode, setTab]);

  const tabs: TabDef[] = [
    ...(mode !== "side" ? [{ value: "transcript" as PanelTab, label: "Transcript" }] : []),
    { value: "shots", label: "Shots", count: model.shots.length },
    { value: "text", label: "Text on screen", count: model.screenText.length },
    {
      value: "people",
      label: "People on screen",
      count: model.facesMode === "off" ? "off" : model.faces.length,
    },
    { value: "objects", label: "Objects", count: model.objects.length },
    { value: "summary", label: "Summary" },
    { value: "iiif", label: "IIIF" },
  ];
  const more: TabDef[] = [
    { value: "speakers", label: "Speakers" },
    { value: "entities", label: "Entities" },
    { value: "chat", label: "Chat" },
    { value: "notes", label: "Notes", count: notesCount || undefined },
    { value: "history", label: "History" },
    ...MORE_TABS.filter((t) => t.value !== "iiif"),
  ];
  const all = [...tabs, ...more];
  const current: PanelTab = all.some((t) => t.value === tab) ? tab : mode === "side" ? "text" : "transcript";
  const src = sourceLabel(rec);
  const meta = [
    homeText(r.ns, rec.collection_path),
    rec.recorded_at ? shortDate(rec.recorded_at) : null,
    tc(model.durationMs),
    model.media.width && model.media.height ? `${model.media.width}×${model.media.height}` : null,
    `${model.shots.length} shots`,
    `${model.screenText.length} lines on screen`,
    model.facesMode === "off" ? null : `${model.faces.length} people on screen`,
  ].filter(Boolean);
  const status = (rec.status ?? "").toLowerCase();

  const body = (
    <>
      {current === "transcript" ? (
        <div className={cn("-mx-5 -my-4 flex flex-col", compact ? "h-[62dvh]" : "h-[70vh]")}>
          <Transcript slim={!compact} compact={compact} />
        </div>
      ) : current === "shots" ? (
        <ShotsTab />
      ) : current === "text" ? (
        <ScreenTextTab why={notes.ocrWhy} />
      ) : current === "people" ? (
        <PeopleTab noFaces={notes.noFaces} />
      ) : current === "objects" ? (
        <ObjectsTab selected={object} onSelect={setObject} why={notes.objectsWhy} />
      ) : (
        <PanelBody tab={current} />
      )}
    </>
  );

  return (
    <div className={cn("flex flex-col", !compact && mode === "side" && "h-[calc(100dvh-4rem)] overflow-hidden")}>
      {compact ? (
        <div className="flex items-center gap-1.5 border-b border-border px-2 pb-2 pt-1">
          <Link
            href="/library"
            aria-label="Back to the Library"
            className="grid size-11 shrink-0 place-items-center rounded-full hover:bg-surface-neutral"
          >
            <ChevronLeft className="size-[22px]" />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[15px] font-bold leading-tight text-fg">{model.title}</h1>
            <p className="tabular truncate text-[12px] text-fg-muted">
              {[homeText(r.ns, rec.collection_path, true), tc(model.durationMs), "video"].filter(Boolean).join(" · ")}
            </p>
          </div>
          <HeaderActions compact />
        </div>
      ) : (
        <header className="flex flex-col gap-1.5 border-b border-border px-6 py-3">
          <div className="flex items-center gap-3.5">
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <div className="flex min-w-0 items-center gap-2.5">
                <h1 className="truncate text-[22px] font-bold leading-[1.2] text-fg">{model.title}</h1>
                <span className="inline-flex h-[22px] shrink-0 items-center gap-1 rounded-[6px] bg-fg px-2 text-[11px] font-bold text-background">
                  <VideoIcon className="size-3" /> VIDEO
                </span>
                <StatusChip status={status} label={status ? status[0].toUpperCase() + status.slice(1) : "Unknown"} />
              </div>
              <p className="tabular truncate text-[13px] leading-none text-fg-secondary">
                {meta.join(" · ")}
                {src && (
                  <>
                    {" · "}
                    <span className={src.file ? "font-mono text-[12px]" : undefined} title={src.title}>
                      {src.text}
                    </span>
                  </>
                )}
              </p>
            </div>
            <HeaderActions />
          </div>
          <Banners />
        </header>
      )}
      {compact && (state.phase === "analyzing" || state.phase === "failed") && <Banners className="mx-3 mt-2" />}
      <div className={cn("min-h-0 flex-1", mode === "side" ? "grid grid-cols-[minmax(0,1fr)_440px]" : "flex flex-col")}>
        <div
          className={cn("flex min-h-0 min-w-0 flex-col", mode === "side" && "overflow-y-auto border-r border-border")}
        >
          <div className={cn("flex flex-col gap-2.5", compact ? "px-0 pt-0" : "px-5 pt-3.5")}>
            <VideoStage
              overlays={mode === "theatre" ? { ...overlays, captions: true } : overlays}
              object={object}
              maxHeight={mode === "theatre" ? "74vh" : compact ? "40vh" : "56vh"}
              className={compact ? "rounded-none" : undefined}
            />
            <div className={compact ? "px-4" : undefined}>
              <VideoControls
                overlays={overlays}
                setOverlay={toggle}
                layout={mode}
                setLayout={setLayout}
                compact={compact}
              />
            </div>
          </div>
          {state.job && isActive(state.job) && (
            <VisualProgress job={state.job} className={compact ? "mx-4 mt-2.5" : "mx-5 mt-2.5"} />
          )}
          <div className={compact ? "mx-4 mt-2.5" : "mx-5 mt-2.5"}>
            <VideoTimeline compact={compact} />
          </div>
          <div className="mt-2.5 flex min-h-[320px] flex-1 flex-col">
            <PanelTabs tabs={tabs} more={more} value={current} onChange={setTab} idBase="video" className="px-3.5" />
            <PanelScroll
              id="video"
              tab={current}
              className={cn(mode === "side" ? "overflow-visible" : "", "gap-2.5 px-5 py-3")}
            >
              {body}
            </PanelScroll>
          </div>
        </div>
        {mode === "side" && (
          <aside aria-label="Transcript" className="flex min-h-0 flex-col">
            <Transcript slim />
          </aside>
        )}
      </div>
    </div>
  );
}

/** VR2 "processing": each visual lane's progress while the job runs, and that you can watch and read meanwhile. */
function VisualProgress({ job, className }: { job: JobInfo; className?: string }) {
  const { model } = useRec();
  const steps = loopSteps(job);
  const cur = currentStep(job);
  const rows = steps.filter((s) => ["shots", "transcribe", "ocr", "faces", "objects"].includes(s.key ?? ""));
  if (!rows.length) return null;
  const count: Record<string, number> = {
    shots: model.shots.length,
    ocr: model.screenText.length,
    faces: model.faces.length,
    objects: model.objects.length,
    transcribe: model.segments.length,
  };
  return (
    <section
      className={cn("flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface px-3.5 py-3", className)}
      aria-label="Processing"
    >
      <dl className="m-0 grid grid-cols-[110px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 text-[12px]">
        {rows.map((s) => (
          <div key={s.key} className="contents">
            <dt className="text-fg-strong">{s.key === "transcribe" ? "Transcript" : s.label}</dt>
            <dd className="m-0">
              <Progress
                value={s.state === "done" ? 1 : s.state === "current" ? undefined : 0}
                tone={s.state === "done" ? "green" : "intent"}
                label={`${s.label}: ${s.state}`}
              />
            </dd>
            <dd
              className={cn(
                "tabular m-0 text-right",
                s.state === "current" ? "text-fg-accent" : s.state === "done" ? "text-green-dark" : "text-fg-muted",
              )}
            >
              {s.state === "done"
                ? `✓ ${count[s.key ?? ""] ?? ""}`
                : s.state === "current"
                  ? "running"
                  : s.state === "skipped"
                    ? "skipped"
                    : "waiting"}
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex items-center gap-3">
        <p className="min-w-0 flex-1 text-[13px] leading-snug text-fg-strong">
          <b className="text-fg">
            {cur === "ocr"
              ? "Shots are ready; text on screen is being read."
              : cur === "faces"
                ? "Looking for people on screen."
                : "Processing the video."}
          </b>{" "}
          Lines and people appear in the lanes and panels as they&apos;re found. You can watch and read now.
        </p>
        <Button asChild variant="ghost" size="sm">
          <Link href={`/activity/${job.id}`}>View job</Link>
        </Button>
      </div>
    </section>
  );
}
