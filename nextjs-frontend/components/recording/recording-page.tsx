"use client";

import { FileQuestion, RotateCw, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { keyToAction } from "@/components/player/keys";
import { PlayerProvider, usePlayerApi, usePlayerState } from "@/components/player/media";
import { AudioLayout } from "@/components/recording/audio-layout";
import { RecordingProvider, type PanelTab, type RecordingCtx } from "@/components/recording/context";
import { usePlayer, useRecording, useRecordingJobs } from "@/components/recording/hooks";
import { pageState } from "@/components/recording/jobs";
import { MobileLayout } from "@/components/recording/mobile-layout";
import { adjacentTurnStart, findInSegments, groupTurns, segmentAt, type EntityRef } from "@/components/recording/model";
import { RecordingSkeleton } from "@/components/recording/skeleton";
import { RecordingDialogs, type DialogState } from "@/components/recording/dialogs";
import { VideoLayout, type VideoCommand } from "@/components/recording/video/video-layout";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/states";
import { ApiError } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { useMediaQuery } from "@/components/player/use-media-query";

/**
 * The recording page (R1–R9, VR1–VR3): loads the recording, its player data and jobs, derives the page state, and
 * renders the audio or the video layout (desktop or phone) around one media clock.
 */
export function RecordingPage({ id, start }: { id: number; start: number | null }) {
  const rec = useRecording(id);
  const jobs = useRecordingJobs(id);
  const state = useMemo(() => pageState(rec.data ?? {}, jobs.data ?? []), [rec.data, jobs.data]);
  const player = usePlayer(id, state.phase === "processing" ? 5000 : false);
  const title = rec.data?.title;
  useEffect(() => {
    if (title) document.title = `${title} · Lens Archive`;
  }, [title]);

  if (rec.isError || player.isError) {
    const e = (rec.error ?? player.error) as unknown;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) {
      return (
        <EmptyState icon={<FileQuestion />} title="This recording isn't here" className="py-24">
          It may have been deleted, or it&apos;s in a namespace you don&apos;t have access to.
          <div className="mt-4">
            <Button asChild variant="secondary">
              <Link href="/library">Back to the Library</Link>
            </Button>
          </div>
        </EmptyState>
      );
    }
    return (
      <EmptyState
        tone="error"
        icon={<TriangleAlert />}
        title="Couldn't load this recording"
        className="py-24"
        actions={
          <Button
            variant="primary"
            icon={<RotateCw />}
            onClick={() => {
              void rec.refetch();
              void player.refetch();
            }}
          >
            Try again
          </Button>
        }
      >
        {e instanceof ApiError ? e.message : "Something went wrong."} The archive server may be busy or unreachable.
      </EmptyState>
    );
  }
  if (!rec.data || !player.data) return <RecordingSkeleton />;

  return (
    <PlayerShell
      key={id}
      id={id}
      start={start}
      rec={rec.data}
      model={player.data}
      state={state}
      jobs={jobs.data ?? []}
    />
  );
}

function PlayerShell(props: Omit<InnerProps, "turns" | "speakers">) {
  const [announcement, setAnnouncement] = useState("");
  const turns = useMemo(() => groupTurns(props.model.segments), [props.model.segments]);
  const speakers = useMemo(() => new Map(props.model.speakers.map((s) => [s.key, s])), [props.model.speakers]);
  // The current line is announced only when someone seeks, never during playback.
  const onSeek = useCallback(
    (ms: number, manual: boolean) => {
      if (!manual) return;
      const i = segmentAt(props.model.segments, ms);
      const seg = i >= 0 ? props.model.segments[i] : null;
      const who = seg?.speaker ? speakers.get(seg.speaker)?.name : null;
      const text = seg ? (seg.text.length > 140 ? `${seg.text.slice(0, 137)}…` : seg.text) : "";
      setAnnouncement(`${tc(ms)}${who ? `, ${who}` : ""}${text ? `: ${text}` : ""}`);
    },
    [props.model.segments, speakers],
  );
  const hasMedia = Boolean(props.model.audio);
  return (
    <PlayerProvider
      hasMedia={hasMedia}
      durationMs={props.model.durationMs}
      speech={props.model.segments}
      onSeek={onSeek}
    >
      <Inner {...props} turns={turns} speakers={speakers} />
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>
    </PlayerProvider>
  );
}

type InnerProps = {
  id: number;
  start: number | null;
  rec: RecordingCtx["rec"];
  model: RecordingCtx["model"];
  state: RecordingCtx["state"];
  jobs: RecordingCtx["jobs"];
  turns: RecordingCtx["turns"];
  speakers: RecordingCtx["speakers"];
};

function Inner({ id, start, rec, model, state, jobs, turns, speakers }: InnerProps) {
  const api = usePlayerApi();
  const { hasMedia } = usePlayerState();
  const { roleIn, can } = useArchive();
  const ns = rec.namespace ?? model.namespace;
  const role = rec.role ?? roleIn(ns);
  const canEdit = role === "editor" || role === "owner" || can("editor", ns);
  const video = model.media.kind === "video";
  const compact = useMediaQuery("(max-width: 1023px)");

  const [findOpen, setFindOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hitIndex, setHitIndex] = useState(0);
  const hits = useMemo(() => findInSegments(model.segments, query), [model.segments, query]);
  const [selected, select] = useState<EntityRef | null>(null);
  const [tab, setTab] = useState<PanelTab>(video ? (compact ? "transcript" : "text") : "summary");
  const [chatDraft, setChatDraft] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState<DialogState>(null);
  const videoCmd = useRef<((c: VideoCommand) => void) | null>(null);

  // ?t= seeks once, when the page opens (search results and chat citations link that way).
  const started = useRef(false);
  useEffect(() => {
    if (started.current || start == null) return;
    started.current = true;
    api.seek(start * 1000, { manual: true });
  }, [api, start]);

  // #access (Home's "Needs attention" and access request emails link that way) opens the access settings.
  useEffect(() => {
    if (window.location.hash === "#access") setDialog({ kind: "access" });
  }, []);

  const value = useMemo<RecordingCtx>(
    () => ({
      id,
      rec,
      model,
      turns,
      speakers,
      state,
      jobs,
      ns,
      role,
      canEdit,
      transcriptOnly: !model.audio,
      find: {
        open: findOpen,
        query,
        hits,
        index: Math.min(hitIndex, Math.max(0, hits.length - 1)),
        setOpen: setFindOpen,
        setQuery,
        setIndex: setHitIndex,
      },
      entity: { selected, select },
      tab,
      setTab,
      chatDraft,
      askInChat: (quote) => {
        setChatDraft(quote);
        setTab("chat");
      },
      clearChatDraft: () => setChatDraft(null),
      editing,
      setEditing,
      openReprocess: () => setDialog({ kind: "reprocess" }),
      openShare: (startMs) => setDialog({ kind: "share", startMs }),
      openRename: () => setDialog({ kind: "rename" }),
      openAccess: () => setDialog({ kind: "access" }),
      openAttach: () => setDialog({ kind: "attach" }),
    }),
    [
      id,
      rec,
      model,
      turns,
      speakers,
      state,
      jobs,
      ns,
      role,
      canEdit,
      findOpen,
      query,
      hits,
      hitIndex,
      selected,
      tab,
      chatDraft,
      editing,
    ],
  );

  // Player shortcuts (handoff): Space, J/L, ←/→, ↑/↓, /; video adds , . Shift+←/→ C F T.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const a = keyToAction(e, video ? "video" : "audio");
      if (!a) return;
      if (document.querySelector("[role=dialog][data-state=open]")) return;
      switch (a.type) {
        case "toggle":
          if (!hasMedia) return;
          api.toggle();
          break;
        case "seekBy":
          api.seekBy(a.ms, { manual: true });
          break;
        case "turn": {
          const t = adjacentTurnStart(turns, api.now() + 1, a.dir);
          if (t == null) return;
          api.seek(t, { manual: true });
          break;
        }
        case "find":
          setFindOpen(true);
          videoCmd.current?.(a);
          break;
        case "frame":
        case "shot":
        case "overlay":
          if (!videoCmd.current) return;
          videoCmd.current(a);
          break;
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [api, hasMedia, turns, video]);

  return (
    <RecordingProvider value={value}>
      {video ? (
        <VideoLayout compact={compact} onCommand={(fn) => (videoCmd.current = fn)} />
      ) : compact ? (
        <MobileLayout />
      ) : (
        <AudioLayout />
      )}
      {!video && model.audio && <AudioElement src={model.audio} />}
      <RecordingDialogs state={dialog} onClose={() => setDialog(null)} />
    </RecordingProvider>
  );
}

/** The one <audio> element; controls are the page's own. It survives switching between the desktop and phone layouts. */
function AudioElement({ src }: { src: string }) {
  const api = usePlayerApi();
  return <audio ref={api.attach} src={src} preload="metadata" className="hidden" />;
}
