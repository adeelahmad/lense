"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Podcast as PodcastIcon, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { Podcasts } from "@/app/openapi-client";
import type { Podcast, PodcastLine } from "@/app/openapi-client/types.gen";
import {
  STAGES,
  hostNames,
  inProgress,
  sourceHref,
  stageIndex,
  statusLabel,
  statusTone,
} from "@/components/podcasts/model";
import { Badge, JobStepChip, type StepState } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Panel } from "@/components/ui/panel";
import { DateTime, EmptyState, PageHeader, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** An episode: how far it has got, its audio, the script with each line's sources, and what the fact-check changed. */
export function EpisodePage({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const { namespaces, can } = useArchive();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const q = useQuery({
    queryKey: ["podcasts", id],
    queryFn: () => data(Podcasts.getPodcast({ client, path: { rid: id } })),
    refetchInterval: (query) => (inProgress(query.state.data?.status) ? 3000 : false),
  });
  const ep = q.data;
  const ns = ep?.namespace ?? namespaces.find((n) => n.id === ep?.space)?.name ?? null;
  const editor = Boolean(ns && can("editor", ns));
  const busy = inProgress(ep?.status);

  const regenerate = useMutation({
    mutationFn: () => data(Podcasts.regeneratePodcast({ client, path: { rid: id } })),
    onSuccess: () => {
      toast({ title: "Making it again", body: "Its sources are read afresh.", tone: "green" });
      void qc.invalidateQueries({ queryKey: ["podcasts"] });
    },
    onError: (e) => toast({ title: "Couldn’t remake it", body: (e as Error).message, tone: "red" }),
  });
  const remove = useMutation({
    mutationFn: () => data(Podcasts.deletePodcast({ client, path: { rid: id } })),
    onSuccess: () => {
      toast({ title: "Podcast deleted", tone: "green" });
      void qc.invalidateQueries({ queryKey: ["podcasts"] });
      router.push("/podcasts");
    },
    onError: (e) => toast({ title: "Couldn’t delete it", body: (e as Error).message, tone: "red" }),
  });

  if (q.isError) {
    const missing = q.error instanceof ApiError && (q.error.status === 404 || q.error.status === 403);
    return (
      <EmptyState
        icon={<CircleAlert />}
        tone="error"
        title={missing ? "No such podcast" : "Couldn’t load this podcast"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/podcasts">All podcasts</Link>
          </Button>
        }
      >
        {missing ? "It was deleted, or it’s in a namespace you can’t read." : (q.error as Error).message}
      </EmptyState>
    );
  }
  if (!ep) {
    return (
      <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-[320px] rounded-md" />
      </div>
    );
  }

  const hosts = hostNames(ep.request);
  const changes = (ep.checks ?? []).filter((c) => c.action === "rewritten" || c.action === "dropped");
  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <div>
        <Link href="/podcasts" className="text-[13px] font-semibold text-fg-accent hover:underline">
          ← Podcasts
        </Link>
        <PageHeader
          className="mb-0 mt-2"
          title={ep.title || (busy ? "New podcast" : "Untitled podcast")}
          meta={
            <span className="inline-flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(ep.status)} dot>
                {statusLabel(ep.status)}
              </Badge>
              {ns && <span>{ns}</span>}
              {ep.duration_ms ? <span className="tabular">{tc(ep.duration_ms)}</span> : null}
              <DateTime iso={ep.created_at} />
            </span>
          }
          actions={
            <>
              <Button
                variant="secondary"
                size="sm"
                icon={<RefreshCw />}
                disabled={!editor || busy || regenerate.isPending}
                disabledReason={
                  !editor
                    ? `Editors of ${ns ?? "its namespace"} can remake it`
                    : busy
                      ? "It’s being made now"
                      : undefined
                }
                onClick={() => regenerate.mutate()}
              >
                Remake
              </Button>
              <Button
                variant="danger-ghost"
                size="sm"
                icon={<Trash2 />}
                disabled={!editor || busy}
                disabledReason={
                  !editor
                    ? `Editors of ${ns ?? "its namespace"} can delete it`
                    : busy
                      ? "Wait until it’s made"
                      : undefined
                }
                onClick={() => setConfirmDelete(true)}
              >
                Delete
              </Button>
            </>
          }
        />
      </div>

      {busy && <Progress status={ep.status} job={ep.job} />}
      {ep.status === "failed" && (
        <Banner tone="error" title="It couldn’t be made.">
          {ep.error || "Something went wrong."} {editor && "Remake it once the problem is fixed."}
        </Banner>
      )}
      {ep.status === "script_only" && (
        <Banner tone="warning" title="No voices yet.">
          Text to speech isn’t set up, so there’s a script but no audio.{" "}
          <Link href="/settings/ai" className="font-semibold text-fg-accent hover:underline">
            Set it up in Settings
          </Link>
          , then remake it.
        </Banner>
      )}

      <Episode ep={ep} hosts={hosts} />

      {(ep.sources?.length ?? 0) > 0 && (
        <Panel title="Sources" subtitle="What the hosts drew on. Each claim in the script cites one of these.">
          <ol className="flex flex-col gap-3">
            {ep.sources?.map((s) => {
              const href = sourceHref({ recording: Number(s.ref.recording), t0: s.t0, page: s.page });
              return (
                <li key={s.n} id={`source-${s.n}`} className="flex gap-3 text-[13.5px]">
                  <span className="tabular w-6 shrink-0 text-right font-bold text-fg-secondary">{s.n}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      {href ? (
                        <Link href={href} className="font-semibold text-fg-accent hover:underline">
                          {s.title || "Untitled"}
                        </Link>
                      ) : (
                        <span className="font-semibold">{s.title || "Untitled"}</span>
                      )}
                      {s.t0 != null && !s.page && <span className="tabular text-fg-muted">{tc(s.t0)}</span>}
                      {s.page ? <span className="text-fg-muted">page {s.page}</span> : null}
                      {s.namespace && <span className="text-fg-muted">{s.namespace}</span>}
                      {s.picked && <Badge>Picked</Badge>}
                    </div>
                    <p className="mt-0.5 line-clamp-3 text-fg-secondary">{s.text}</p>
                  </div>
                </li>
              );
            })}
          </ol>
        </Panel>
      )}

      {changes.length > 0 && (
        <Panel
          title="Fact-check changes"
          subtitle="Lines the check found weren’t backed by their sources, and what it did about them."
        >
          <ul className="flex flex-col gap-3">
            {changes.map((c, i) => (
              <li key={i} className="text-[13.5px]">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={c.action === "dropped" ? "red" : "gate"}>{actionLabel(c.action)}</Badge>
                  <span className="text-fg-muted">{c.verdict}</span>
                </div>
                {c.before && <p className="mt-1 text-fg-muted line-through">{c.before}</p>}
                {c.after && <p className="mt-0.5 text-fg-strong">{c.after}</p>}
                {c.why && <p className="mt-0.5 text-[12.5px] text-fg-secondary">{c.why}</p>}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Dialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this podcast?"
        description="Its script and audio go. Its sources stay as they are."
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
              Delete
            </Button>
          </>
        }
      />
    </div>
  );
}

function actionLabel(action: string): string {
  return action === "dropped" ? "Dropped" : action === "rewritten" ? "Rewritten" : action;
}

function Progress({ status, job }: { status: string; job?: number | null }) {
  const at = stageIndex(status);
  return (
    <Panel tone="intent">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-[13.5px] font-semibold text-fg">{at < 0 ? "Waiting to start" : "Making it"}</span>
        {STAGES.map((s, i) => {
          const state: StepState = i < at ? "done" : i === at ? "running" : "waiting";
          return <JobStepChip key={s.status} step={s.label} state={state} />;
        })}
        {job != null && (
          <Link href={`/activity/${job}`} className="ml-auto text-[13px] font-semibold text-fg-accent hover:underline">
            Details in Activity
          </Link>
        )}
      </div>
    </Panel>
  );
}

/** The player and the script; the line being played is highlighted, and a line's time plays from there. */
function Episode({ ep, hosts }: { ep: Podcast; hosts: { a: string; b: string } }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [now, setNow] = useState(-1);
  const lines = ep.lines ?? [];
  const playing = lines.findIndex((l) => l.t0 != null && l.t1 != null && now >= l.t0 && now < l.t1);
  if (!lines.length) {
    if (inProgress(ep.status)) return <Skeleton className="h-[240px] rounded-md" />;
    return (
      <EmptyState icon={<PodcastIcon />} title="No script">
        Nothing was written for this episode.
      </EmptyState>
    );
  }
  const seek = (l: PodcastLine) => {
    const a = audio.current;
    if (!a || l.t0 == null) return;
    a.currentTime = l.t0 / 1000;
    void a.play().catch(() => undefined);
  };
  return (
    <Panel title="Script" subtitle={`${hosts.a} explains, ${hosts.b} asks.`}>
      {ep.audio && (
        <audio
          ref={audio}
          controls
          preload="metadata"
          src={ep.audio}
          className="sticky top-2 z-10 mb-4 w-full"
          onTimeUpdate={(e) => setNow(e.currentTarget.currentTime * 1000)}
        />
      )}
      <ol className="flex flex-col gap-3">
        {lines.map((l, i) => (
          <li
            key={l.idx}
            className={cn(
              "flex gap-3 rounded-sm px-2 py-1.5 text-[14.5px] leading-relaxed",
              i === playing && "bg-blue-surface",
            )}
          >
            <div className="w-16 shrink-0">
              <div className={cn("text-[13px] font-bold", l.speaker === "a" ? "text-blue-dark" : "text-green-dark")}>
                {l.speaker === "a" ? hosts.a : hosts.b}
              </div>
              {ep.audio && l.t0 != null && (
                <button
                  type="button"
                  onClick={() => seek(l)}
                  className="tabular text-[12px] text-fg-muted hover:text-fg-accent hover:underline"
                  aria-label={`Play from ${tc(l.t0)}`}
                >
                  {tc(l.t0)}
                </button>
              )}
            </div>
            <p className="min-w-0 flex-1 text-fg">
              {l.text}
              {(l.sources ?? []).map((s) => {
                const href = sourceHref({
                  recording: s.recording,
                  t0: s.t0 as number | null | undefined,
                  page: s.page as number | null | undefined,
                });
                return href ? (
                  <Link
                    key={s.n}
                    href={href}
                    className="ml-1 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-pill bg-surface-neutral px-1 align-[1px] text-[11px] font-bold text-fg-secondary hover:bg-blue-surface hover:text-blue-dark"
                    aria-label={`Source ${s.n}`}
                  >
                    {s.n}
                  </Link>
                ) : null;
              })}
            </p>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
