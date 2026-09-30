"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { Loader2, Pause, Play, Search as SearchIcon, X } from "lucide-react";
import Link from "next/link";

import { Entities, Search } from "@/app/openapi-client";
import { nameTone } from "@/components/chat/citation";
import { shortTitle } from "@/components/chat/cite";
import { connections, typeLabel, type GraphEdge, type GraphNode } from "@/components/graph/model";
import { NodeIcon } from "@/components/graph/shape";
import { useRecordingIndex } from "@/components/search/data";
import { hasMedia, recordingHref } from "@/components/search/links";
import { InlinePlayerBar, useInlinePlayer, type InlinePlayer } from "@/components/search/player";
import { talkTime } from "@/components/speakers/format";
import { useSpeakerRecordings } from "@/components/speakers/parts";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural, shortDate, tc } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

function PlayMoment({ player, k, recordingId, t0, title }: { player: InlinePlayer; k: string; recordingId: number; t0: number; title?: string | null }) {
  const index = useRecordingIndex();
  const none = hasMedia(index.byId.get(recordingId)) === false || player.noAudio.has(recordingId);
  const playing = player.isPlaying(k);
  return (
    <button
      type="button"
      aria-label={none ? "No audio — transcript only" : `${playing ? "Pause" : "Play"} from ${tc(t0)}`}
      aria-disabled={none || undefined}
      onClick={() => !none && player.play({ key: k, recordingId, t0, title })}
      className={cn("grid size-[26px] shrink-0 place-items-center rounded-full bg-surface-neutral text-fg [&_svg]:size-[11px]", none ? "cursor-not-allowed opacity-40" : "hover:bg-blue-surface hover:text-blue", playing && "bg-blue text-white")}
    >
      {player.loading === k ? <Loader2 className="animate-spin" /> : playing ? <Pause /> : <Play className="translate-x-px" />}
    </button>
  );
}

function EntityMentions({ n, player }: { n: GraphNode; player: InlinePlayer }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["mentions", n.refs.join(",")],
    queryFn: () => data(Search.listMentions({ client, query: { entities: n.refs.slice(0, 50).join(",") } })),
    staleTime: 60_000,
  });
  if (q.isLoading) return <Skeleton className="h-24 w-full" />;
  if (q.isError) return <Banner tone="error">{q.error.message}</Banner>;
  const items = q.data ?? [];
  if (!items.length) return <p className="m-0 text-[13px] text-fg-secondary">No mentions you can read.</p>;
  return (
    <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
      {items.slice(0, 12).map((m, i) => (
        <li key={i} className="grid grid-cols-[26px_minmax(0,1fr)] items-start gap-2">
          <PlayMoment player={player} k={`m${i}-${m.recording_id}-${m.t0}`} recordingId={m.recording_id} t0={m.t0 ?? 0} title={m.title} />
          <span className="flex min-w-0 flex-col gap-[3px]">
            <Link href={recordingHref(m.recording_id, m.t0)} className="text-[12px] font-semibold leading-tight text-fg-secondary hover:text-fg-accent hover:underline">
              {shortTitle(m.title)} · {tc(m.t0)}
              {m.speaker && (
                <>
                  {" · "}
                  <span style={{ color: nameTone(m.speaker) }}>{m.speaker}</span>
                </>
              )}
            </Link>
            <span className="font-serif text-[13.5px] leading-[1.4] text-fg">{m.text}</span>
          </span>
        </li>
      ))}
      {items.length > 12 && (
        <li className="text-[12.5px] text-fg-muted">
          <Link href={`/search?q=${encodeURIComponent(`"${n.label}"`)}`} className="font-semibold text-fg-accent hover:underline">
            {count(items.length - 12)}+ more mentions in Search →
          </Link>
        </li>
      )}
    </ul>
  );
}

function SpeakerRecordings({ n, player }: { n: GraphNode; player: InlinePlayer }) {
  const recs = useSpeakerRecordings(n.refs[0]);
  if (recs.isLoading) return <Skeleton className="h-20 w-full" />;
  if (recs.isError) return <Banner tone="error">{recs.error.message}</Banner>;
  return (
    <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
      {(recs.data ?? []).slice(0, 8).map((r) => (
        <li key={r.id} className="grid grid-cols-[26px_minmax(0,1fr)] items-start gap-2">
          <PlayMoment player={player} k={`r${r.id}`} recordingId={r.id} t0={r.first_t0 ?? 0} title={r.title} />
          <span className="flex min-w-0 flex-col gap-[3px]">
            <Link href={recordingHref(r.id, r.first_t0)} className="truncate text-[13px] font-semibold text-fg hover:text-fg-accent hover:underline">
              {r.title ?? `Recording ${r.id}`}
            </Link>
            <span className="text-[12px] text-fg-muted">
              {shortDate(r.recorded_at)} · {talkTime(r.talk_ms)} talking
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The node panel: what it is, where it's mentioned (playable), what it's connected to, and what you can do with it. */
export function NodePanel({ node, nodes, edges, onSelect, onClose }: { node: GraphNode; nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (id: string) => void; onClose: () => void }) {
  const client = useApiClient();
  const { can } = useArchive();
  const player = useInlinePlayer();
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = connections(edges, node.id);
  const isSpeaker = node.kind === "speaker";
  const details = useQueries({
    queries: (isSpeaker ? [] : node.refs.slice(0, 5)).map((id) => ({
      queryKey: ["entity", id],
      queryFn: () => data(Entities.getEntity({ client, path: { eid: id } })),
      staleTime: 60_000,
    })),
  });
  const recs = details.reduce((a, d) => a + (d.data?.recordings ?? 0), 0);
  const nsList = node.ns.join(", ");
  const canRun = node.ns.some((ns) => can("editor", ns));
  const sub = isSpeaker
    ? `Speaker · ${talkTime(node.weight * 60000)} talk time · ${plural(node.recordings ?? 0, "recording")}`
    : `${typeLabel(node)} · ${plural(node.weight, "mention")}${recs ? ` in ${plural(recs, "recording")}` : ""}`;
  const runHref = isSpeaker
    ? `/batches/new?speaker=${node.refs[0]}&ns=${encodeURIComponent(node.ns[0] ?? "")}`
    : `/batches/new?entity=${node.refs.join(",")}&label=${encodeURIComponent(node.label)}`;
  const runCount = isSpeaker ? (node.recordings ?? 0) : recs;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2.5">
        <NodeIcon n={node} size={16} />
        <h2 className="min-w-0 flex-1 truncate text-[17px] font-bold leading-tight text-fg">{node.label}</h2>
        <IconButton label="Close" size={30} onClick={onClose}>
          <X />
        </IconButton>
      </div>
      <p className="m-0 -mt-1.5 text-[12.5px] leading-snug text-fg-muted">
        {sub}
        {nsList && ` · ${nsList}`}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {isSpeaker ? (
          <Button asChild size="xs" variant="secondary">
            <Link href={`/speakers/${node.refs[0]}`}>Profile</Link>
          </Button>
        ) : (
          <Button asChild size="xs" variant="secondary">
            <Link href={`/search?q=${encodeURIComponent(`"${node.label}"`)}`}>
              <SearchIcon /> Search
            </Link>
          </Button>
        )}
        <Button asChild={canRun} size="xs" variant="ghost" disabled={!canRun} disabledReason={needRole("editor", node.ns[0])} icon={canRun ? undefined : <Play />}>
          {canRun ? (
            <Link href={runHref}>
              <Play /> Run on {runCount || "…"}
            </Link>
          ) : (
            `Run on ${runCount}`
          )}
        </Button>
      </div>
      <h3 className="pt-1 text-[12px] font-bold text-fg-secondary">{isSpeaker ? "Recordings" : "Mentions"}</h3>
      {isSpeaker ? <SpeakerRecordings n={node} player={player} /> : <EntityMentions n={node} player={player} />}
      <InlinePlayerBar player={player} />
      {links.length > 0 && (
        <>
          <h3 className="pt-1 text-[12px] font-bold text-fg-secondary">Connected</h3>
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
            {links.slice(0, 16).map((l) => (
              <li key={l.id}>
                <button
                  type="button"
                  onClick={() => onSelect(l.id)}
                  className="inline-flex h-[26px] items-center rounded-pill border border-border px-2.5 text-[12.5px] font-medium text-fg hover:border-blue-border hover:bg-blue-surface"
                >
                  {byId.get(l.id)?.label ?? l.id} · {l.kind === "maybe the same voice" ? "maybe same voice" : count(l.w)}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
