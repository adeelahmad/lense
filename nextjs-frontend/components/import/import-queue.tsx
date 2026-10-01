"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ClipboardPaste, FileAudio, FileText, FileVideo } from "lucide-react";
import Link from "next/link";

import { Jobs } from "@/app/openapi-client";
import { kindOf } from "@/components/import/files";
import { sentShare, sentText } from "@/components/import/upload-model";
import { stepLabel } from "@/components/library/model";
import type { QueueItem } from "@/components/import/use-import";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

type RowState = {
  step: string;
  pct: string;
  width: number | null;
  badge: string;
  tone: Tone;
  bar: "intent" | "muted" | "green" | "red";
};

function QueueRow({
  q,
  onPause,
  onResume,
}: {
  q: QueueItem;
  onPause: (key: string) => void;
  onResume: (key: string) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const job = useQuery({
    queryKey: ["job", q.job],
    queryFn: () => data(Jobs.getJob({ client, path: { jid: q.job as number } })),
    enabled: q.job != null,
    refetchInterval: (s) => (s.state.data && !["queued", "running"].includes(s.state.data.status) ? false : 2000),
  });
  const j = job.data;
  let st: RowState;
  const share = sentShare(q.sent, q.size);
  if (q.state === "uploading" && q.kind === "media")
    st = {
      step: `Upload · ${sentText(q.sent ?? 0, q.size ?? 0)}`,
      pct: `${Math.floor(share * 100)}%`,
      width: Math.max(0.02, share),
      badge: "Uploading",
      tone: "intent",
      bar: "intent",
    };
  else if (q.state === "uploading")
    st = {
      step: "Upload",
      pct: "sending…",
      width: null,
      badge: "Uploading",
      tone: "intent",
      bar: "muted",
    };
  else if (q.state === "paused")
    st = {
      step: `Paused · ${sentText(q.sent ?? 0, q.size ?? 0)}`,
      pct: `${Math.floor(share * 100)}%`,
      width: Math.max(0.02, share),
      badge: "Paused",
      tone: "neutral",
      bar: "muted",
    };
  else if (q.state === "failed" && q.kind === "media")
    st = {
      step: `Upload stopped · ${sentText(q.sent ?? 0, q.size ?? 0)}`,
      pct: "",
      width: Math.max(0.02, share),
      badge: "Failed",
      tone: "red",
      bar: "red",
    };
  else if (q.state === "failed")
    st = {
      step: "Import",
      pct: "",
      width: 1,
      badge: "Failed",
      tone: "red",
      bar: "red",
    };
  else if (q.duplicate && !q.job)
    st = {
      step: "Already in the archive",
      pct: "",
      width: 1,
      badge: "Already here",
      tone: "green",
      bar: "green",
    };
  else if (!j)
    st = {
      step: "Queued",
      pct: "",
      width: 0.05,
      badge: "Queued",
      tone: "neutral",
      bar: "intent",
    };
  else if (j.status === "succeeded")
    st = {
      step: "Done",
      pct: "100%",
      width: 1,
      badge: "Ready",
      tone: "green",
      bar: "green",
    };
  else if (j.status === "failed")
    st = {
      step: `${stepLabel(j.next_step)} failed`,
      pct: "",
      width: j.progress ?? 0,
      badge: "Failed",
      tone: "red",
      bar: "red",
    };
  else if (j.status === "cancelled")
    st = {
      step: "Cancelled",
      pct: "",
      width: j.progress ?? 0,
      badge: "Cancelled",
      tone: "neutral",
      bar: "muted",
    };
  else {
    const pct = Math.round((j.progress ?? 0) * 100);
    st =
      j.status === "queued"
        ? {
            step: stepLabel(j.next_step),
            pct: "waiting for a worker",
            width: Math.max(0.03, j.progress ?? 0),
            badge: "Queued",
            tone: "neutral",
            bar: "intent",
          }
        : {
            step: stepLabel(j.next_step),
            pct: `${pct}%`,
            width: Math.max(0.04, j.progress ?? 0),
            badge: "Processing",
            tone: "intent",
            bar: "intent",
          };
  }
  const Icon =
    q.kind === "paste"
      ? ClipboardPaste
      : q.kind === "media"
        ? kindOf(q.name) === "video"
          ? FileVideo
          : FileAudio
        : FileText;
  const error = q.error ?? (j?.status === "failed" ? j.error : null);
  return (
    <li className="grid grid-cols-[28px_minmax(0,1fr)] items-center gap-x-3.5 gap-y-2 border-b border-border px-4 py-3.5 last:border-b-0 md:grid-cols-[28px_minmax(0,1.3fr)_minmax(0,1fr)_150px_80px] md:px-6">
      <Icon className="size-[18px] text-fg-secondary" aria-hidden />
      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className="truncate text-[14px] font-semibold leading-tight text-fg">{j?.title || q.title}</span>
        <span className="truncate text-[12px] leading-tight text-fg-muted">
          {q.name} · {q.namespace}
        </span>
        {error && <span className="text-[12px] leading-snug text-red-dark">{error}</span>}
      </span>
      <span className="col-start-2 flex flex-col gap-1.5 md:col-start-auto">
        <span className="tabular flex justify-between gap-2 text-[12.5px] font-medium leading-none text-fg-secondary">
          <span className="truncate">{st.step}</span>
          <span className="shrink-0">{st.pct}</span>
        </span>
        <span
          className="relative block h-[5px] overflow-hidden rounded-pill bg-surface-neutral"
          role="progressbar"
          aria-label={`${q.title}: ${st.step}`}
          aria-valuenow={st.width == null ? undefined : Math.round(st.width * 100)}
        >
          {st.width == null ? (
            <span className="absolute inset-y-0 left-[20%] w-[30%] animate-pulse rounded-pill bg-fg-muted" />
          ) : (
            <span
              className={cn(
                "block h-full rounded-pill transition-[width] duration-slow",
                {
                  intent: "bg-blue",
                  muted: "bg-fg-muted",
                  green: "bg-green",
                  red: "bg-red",
                }[st.bar],
              )}
              style={{ width: `${Math.round(st.width * 100)}%` }}
            />
          )}
        </span>
      </span>
      <span className="col-start-2 flex items-center justify-between gap-2 md:col-start-auto md:contents">
        <span>
          <Badge tone={st.tone} dot>
            {st.badge}
          </Badge>
        </span>
        <span className="text-right text-[13px] font-semibold">
          {q.kind === "media" && q.state === "uploading" ? (
            <button type="button" className="text-fg-accent hover:underline" onClick={() => onPause(q.key)}>
              Pause
            </button>
          ) : q.kind === "media" && (q.state === "paused" || q.state === "failed") ? (
            <button type="button" className="text-fg-accent hover:underline" onClick={() => onResume(q.key)}>
              {q.state === "paused" ? "Resume" : "Try again"}
            </button>
          ) : q.recording != null ? (
            j?.status === "failed" ? (
              <button
                type="button"
                className="text-fg-accent hover:underline"
                onClick={async () => {
                  try {
                    await data(Jobs.retryJob({ client, path: { jid: q.job as number } }));
                    void qc.invalidateQueries({ queryKey: ["job", q.job] });
                  } catch (e) {
                    toast({
                      title: "Couldn’t retry",
                      body: (e as Error).message,
                      tone: "red",
                    });
                  }
                }}
              >
                Retry
              </button>
            ) : (
              <Link href={`/recordings/${q.recording}`} className="text-fg-accent hover:underline">
                Open
              </Link>
            )
          ) : null}
        </span>
      </span>
    </li>
  );
}

/** Library I4: after submitting. Safe to leave; progress also shows in Activity and in the Library rows. */
export function ImportQueue({
  queue,
  onMore,
  onPause,
  onResume,
}: {
  queue: QueueItem[];
  onMore: () => void;
  onPause: (key: string) => void;
  onResume: (key: string) => void;
}) {
  const sending = queue.filter((q) => q.state === "uploading").length;
  const paused = queue.filter((q) => q.state === "paused").length;
  const failed = queue.filter((q) => q.state === "failed").length;
  return (
    <div className="overflow-hidden rounded-md border border-border bg-background">
      <div
        className="flex flex-wrap items-center gap-3 border-b border-border bg-blue-surface px-4 py-4 md:px-6"
        role="status"
        aria-live="polite"
      >
        <CheckCircle2 className="size-[18px] shrink-0 text-blue" aria-hidden />
        <span className="min-w-0 flex-1 text-[14px] leading-snug text-fg">
          {sending ? (
            <>
              <b className="font-bold">Sending {plural(sending, "file")}…</b> Keep this page open until they’re sent;
              processing then carries on without it.
            </>
          ) : paused ? (
            <>
              <b className="font-bold">{plural(paused, "upload")} paused.</b> What arrived is kept for a while: resume
              here, or drop the same file again later to carry on.
            </>
          ) : (
            <>
              <b className="font-bold">{plural(queue.length - failed, "import")} sent.</b> You can leave this page —
              processing continues in Activity and the Library rows.
            </>
          )}
        </span>
        <Button asChild variant="secondary" size="sm">
          <Link href="/library">Go to Library</Link>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onMore}
          disabled={sending > 0}
          disabledReason="Wait until the files are sent"
        >
          Import more
        </Button>
      </div>
      <ul aria-label="Imports">
        {queue.map((q) => (
          <QueueRow key={q.key} q={q} onPause={onPause} onResume={onResume} />
        ))}
      </ul>
    </div>
  );
}
