"use client";

import {
  ChevronDown,
  ChevronRight,
  Clock3,
  Cloud,
  CodeXml,
  Copy,
  Download,
  Ellipsis,
  ExternalLink,
  Files,
  FileText,
  Folder,
  FolderTree,
  Globe,
  Keyboard,
  Link2,
  ListChecks,
  NotebookPen,
  Paperclip,
  Pencil,
  RefreshCw,
  RotateCw,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, type ReactNode } from "react";

import { AccessBadge } from "@/components/access/access-fields";
import { libraryHref } from "@/components/library/collections-model";
import { usePlayerApi } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { pageAt, pagesSummary } from "@/components/recording/document/model";
import { useExport, usePipelines, useRecordingActions } from "@/components/recording/hooks";
import { isActive, readyBefore, shortError, stepLabel, loopSteps } from "@/components/recording/jobs";
import { Badge, StatusChip } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { StepLoop } from "@/components/ui/loop";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { absolute, shortDate, tc } from "@/lib/format";
import { EXPORTS, failureImpact, sourceLabel } from "@/components/recording/labels";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** Header (R1): breadcrumb, title, actions, the meta line, and the running / failed banners. */
export function RecordingHeader() {
  const r = useRec();
  const { rec, model, ns, transcriptOnly, canEdit, openRename, openAccess } = r;
  const pipelines = usePipelines();
  const source = sourceLabel(rec);
  const pipeline = pipelines.data?.pipelines.find((p) => ns && p.namespaces?.includes(ns));
  const status = (rec.status ?? "").toLowerCase();

  return (
    <header className="flex flex-col gap-2 border-b border-border px-6 py-3.5">
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-[12px] font-medium text-fg-muted">
        <Link href="/library" className="shrink-0 text-fg-secondary hover:text-fg hover:underline">
          Library
        </Link>
        <ChevronRight aria-hidden className="size-3 shrink-0" />
        {ns ? (
          <Link
            href={libraryHref(ns)}
            className="shrink-0 hover:text-fg hover:underline"
            aria-label={`${ns} in the Library`}
          >
            {ns}
          </Link>
        ) : (
          <span>Recording</span>
        )}
        {ns &&
          (rec.collection_path ?? []).map((c) => (
            <Fragment key={c.id}>
              <ChevronRight aria-hidden className="size-3 shrink-0" />
              <Link
                href={libraryHref(ns, c.id)}
                className="min-w-0 truncate hover:text-fg hover:underline"
                aria-label={`The ${c.name} collection in the Library`}
              >
                {c.name}
              </Link>
            </Fragment>
          ))}
      </nav>
      <div className="flex items-center gap-4">
        <h1 className="flex min-w-0 items-center gap-2 text-[24px] font-bold leading-[1.2] tracking-[-.015em] text-fg">
          <span className="truncate" title={model.title}>
            {model.title}
          </span>
          <Tooltip content={canEdit ? "Rename" : needRole("editor", ns)}>
            <button
              type="button"
              aria-label="Rename recording"
              aria-disabled={!canEdit || undefined}
              onClick={canEdit ? openRename : undefined}
              className={cn(
                "grid size-7 shrink-0 place-items-center rounded-sm text-fg-muted",
                canEdit ? "hover:bg-surface-neutral hover:text-fg" : "cursor-not-allowed opacity-60",
              )}
            >
              <Pencil className="size-[15px]" />
            </button>
          </Tooltip>
        </h1>
        <div className="flex-1" />
        <HeaderActions />
      </div>
      <div className="tabular flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-[13px] leading-snug text-fg-secondary">
        {ns && (
          <Meta icon={<Folder />}>
            <span>{ns}</span>
          </Meta>
        )}
        {rec.recorded_at && (
          <time dateTime={rec.recorded_at} title={absolute(rec.recorded_at)}>
            {shortDate(rec.recorded_at, true)}
          </time>
        )}
        {r.paged ? (
          <Meta icon={<Files />}>{pagesSummary(model.pages, model.media.kind === "image" ? "image" : "document")}</Meta>
        ) : (
          <Meta icon={<Clock3 />}>{tc(model.durationMs)}</Meta>
        )}
        {rec.attached_to && (
          <Meta icon={<Paperclip />}>
            <span>
              Attached to{" "}
              <Link
                href={`/resources/${rec.attached_to.resource}`}
                className="font-semibold text-fg-accent hover:underline"
              >
                {rec.attached_to.title || "an email"}
              </Link>
            </span>
          </Meta>
        )}
        {source && (
          <Meta icon={source.href ? <Globe /> : source.remote ? <Cloud /> : <FileText />}>
            {source.href ? (
              <a
                href={source.href}
                target="_blank"
                rel="noopener noreferrer"
                className="max-w-[320px] truncate font-semibold text-fg-accent hover:underline"
                title={source.title}
              >
                {source.text}
              </a>
            ) : (
              <span
                className={cn("max-w-[320px] truncate", source.file && "font-mono text-[12px]")}
                title={source.title}
              >
                {source.text}
              </span>
            )}
          </Meta>
        )}
        <Meta icon={<Workflow />}>
          <span title={ns ? `The pipeline new recordings in ${ns} go through` : undefined}>
            {pipeline ? pipeline.name : "Standard pipeline"}
            {pipeline && (
              <span className="ml-1 font-mono text-[11px] font-medium text-fg-muted">v{pipeline.current}</span>
            )}
          </span>
        </Meta>
        <StatusChip status={status} label={status ? status[0].toUpperCase() + status.slice(1) : "Unknown"} />
        <AccessBadge value={rec} onClick={openAccess} />
        {transcriptOnly && <Badge tone="neutral">Transcript only</Badge>}
      </div>
      <Banners />
    </header>
  );
}

function Meta({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="flex items-center gap-[5px] [&>svg]:size-3.5 [&>svg]:shrink-0">
      {icon}
      {children}
    </span>
  );
}

export function HeaderActions({ compact }: { compact?: boolean }) {
  const r = useRec();
  const api = usePlayerApi();
  const { canEdit, ns, openShare, paged, id } = r;
  return (
    <div className="flex items-center gap-2">
      {!compact && (
        <Button asChild variant="secondary" size="sm" icon={<NotebookPen />}>
          <Link href={`/notes/about/recording/${id}`}>Page</Link>
        </Button>
      )}
      {!compact && !paged && (
        <Button
          variant="secondary"
          size="sm"
          icon={<CodeXml />}
          onClick={() => openShare(Math.floor(api.now()) || undefined)}
        >
          Share / Embed
        </Button>
      )}
      {!compact && <ExportMenu />}
      {!compact && <ReprocessMenu disabled={!canEdit} reason={needRole("editor", ns)} />}
      <MoreMenu compact={compact} />
    </div>
  );
}

export function ExportMenu({ trigger }: { trigger?: ReactNode }) {
  const { id, model, rec, paged } = useRec();
  const exp = useExport(id);
  // a document's text has no times for subtitles
  const formats = paged ? EXPORTS.filter(([fmt]) => fmt !== "srt" && fmt !== "vtt") : EXPORTS;
  return (
    <Menu>
      <MenuTrigger asChild>
        {trigger ?? (
          <Button variant="secondary" size="sm" icon={<Download />} disabled={exp.isPending}>
            Export
          </Button>
        )}
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>{paged ? "Download the text" : "Download the transcript"}</MenuLabel>
        {formats.map(([fmt, label]) => (
          <MenuItem key={fmt} onSelect={() => exp.mutate({ fmt, title: model.title })} shortcut={`.${fmt}`}>
            {label}
          </MenuItem>
        ))}
        {rec.report_url && (
          <>
            <MenuSeparator />
            <MenuItem asChild>
              <a href={rec.report_url} target="_blank" rel="noreferrer" className="flex items-center gap-2.5">
                <ExternalLink />
                <span className="flex-1">Open the recording report</span>
              </a>
            </MenuItem>
          </>
        )}
      </MenuContent>
    </Menu>
  );
}

export function ReprocessMenu({ disabled, reason }: { disabled: boolean; reason: string }) {
  const { jobs, ns, openReprocess, id } = useRec();
  const pipelines = usePipelines();
  const { reprocess } = useRecordingActions(id);
  const toast = useToast();
  const router = useRouter();
  const running = jobs.find(isActive);
  const off = disabled || Boolean(running);
  const why = disabled
    ? reason
    : running
      ? "A job is already running for this recording; follow it in Activity"
      : undefined;
  const nsPipeline = pipelines.data?.pipelines.find((p) => ns && p.namespaces?.includes(ns));
  const run = (body: { pipeline?: number }, label: string) =>
    reprocess.mutate(body, {
      onSuccess: (res) =>
        toast({
          title: `${label} queued`,
          body: "The step timeline shows progress; the job is also in Activity.",
          tone: "intent",
          action: {
            label: "View job",
            onClick: () => router.push(`/activity/${res.job}`),
          },
        }),
    });
  if (off)
    return (
      <Button variant="secondary" size="sm" icon={<RefreshCw />} disabled disabledReason={why}>
        Reprocess <ChevronDown className="!size-3.5" />
      </Button>
    );
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="secondary" size="sm" icon={<RefreshCw />}>
          Reprocess <ChevronDown className="!size-3.5" />
        </Button>
      </MenuTrigger>
      <MenuContent className="min-w-[260px]">
        <MenuItem icon={<ListChecks />} onSelect={openReprocess}>
          Choose steps…
        </MenuItem>
        <MenuSeparator />
        <MenuLabel>Run a whole pipeline</MenuLabel>
        <MenuItem
          icon={<Workflow />}
          onSelect={() => run({ pipeline: nsPipeline?.id }, nsPipeline ? nsPipeline.name : "Standard pipeline")}
        >
          {nsPipeline ? `${nsPipeline.name} (${ns}'s default)` : `Standard${ns ? ` (${ns}'s default)` : ""}`}
        </MenuItem>
        {(pipelines.data?.pipelines ?? [])
          .filter((p) => p.id !== nsPipeline?.id)
          .map((p) => (
            <MenuItem key={p.id} icon={<Workflow />} onSelect={() => run({ pipeline: p.id }, p.name)}>
              {p.name} <span className="font-mono text-[11px] text-fg-muted">v{p.current}</span>
            </MenuItem>
          ))}
      </MenuContent>
    </Menu>
  );
}

function MoreMenu({ compact }: { compact?: boolean }) {
  const r = useRec();
  const api = usePlayerApi();
  const toast = useToast();
  const latest = r.jobs[0];
  const copy = async (withTime: boolean) => {
    const t = Math.floor(api.now() / 1000);
    const page = r.paged ? pageAt(r.model.segments, api.now()) + 1 : 0; // a document's: the page with the text in view
    const at = r.paged ? (page > 1 ? `?page=${page}` : "") : t > 0 ? `?t=${t}` : "";
    const url = `${window.location.origin}/resources/${r.id}${withTime ? at : ""}`;
    try {
      await navigator.clipboard.writeText(url);
      toast({
        title: withTime ? `Link at ${r.paged ? `page ${page}` : tc(t * 1000)} copied` : "Link copied",
        tone: "green",
      });
    } catch {
      toast({ title: "Couldn't copy", body: url, tone: "red" });
    }
  };
  return (
    <Menu>
      <MenuTrigger asChild>
        <IconButton label="More actions" size={32}>
          <Ellipsis />
        </IconButton>
      </MenuTrigger>
      <MenuContent className="min-w-[240px]">
        <MenuItem icon={<Link2 />} onSelect={() => void copy(false)}>
          Copy link
        </MenuItem>
        <MenuItem icon={<Copy />} onSelect={() => void copy(true)}>
          {r.paged ? "Copy link to this page" : "Copy link at the current time"}
        </MenuItem>
        <MenuItem icon={<FolderTree />} disabled={!r.canEdit || !r.ns} onSelect={r.openCollection}>
          Move to collection…
        </MenuItem>
        {compact && (
          <>
            {!r.paged && (
              <MenuItem icon={<CodeXml />} onSelect={() => r.openShare(Math.floor(api.now()) || undefined)}>
                Share / Embed
              </MenuItem>
            )}
            <MenuItem icon={<RefreshCw />} disabled={!r.canEdit} onSelect={r.openReprocess}>
              Reprocess…
            </MenuItem>
            <MenuItem icon={<Pencil />} disabled={!r.canEdit} onSelect={r.openRename}>
              Rename…
            </MenuItem>
            {r.transcriptOnly && (
              <MenuItem icon={<Paperclip />} disabled={!r.canEdit} onSelect={r.openAttach}>
                Attach audio…
              </MenuItem>
            )}
            <MenuItem icon={<Globe />} onSelect={r.openAccess}>
              Access…
            </MenuItem>
            <MenuItem icon={<RotateCw />} onSelect={() => r.setTab("history")}>
              History
            </MenuItem>
          </>
        )}
        <MenuSeparator />
        {latest && (
          <MenuItem asChild>
            <Link href={`/activity/${latest.id}`} className="flex items-center gap-2.5">
              <Workflow />
              <span className="flex-1">Latest run in Activity</span>
            </Link>
          </MenuItem>
        )}
        {r.rec.report_url && (
          <MenuItem asChild>
            <a href={r.rec.report_url} target="_blank" rel="noreferrer" className="flex items-center gap-2.5">
              <ExternalLink />
              <span className="flex-1">Recording report</span>
            </a>
          </MenuItem>
        )}
        <MenuItem icon={<FileText />} onSelect={() => r.setTab("details")}>
          Details
        </MenuItem>
        <MenuItem icon={<Keyboard />} shortcut="?" onSelect={() => window.dispatchEvent(new Event("lens:shortcuts"))}>
          Keyboard shortcuts
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}

/** R8 (running, just imported) and R6 (a step failed) banners under the meta line. */
export function Banners({ className }: { className?: string }) {
  const { state, canEdit, ns, id } = useRec();
  const { retry, reprocess } = useRecordingActions(id);
  const job = state.job;
  if (state.phase === "analyzing" && job) {
    const step = stepLabel(job.nextStep ?? job.steps[job.stepIndex]?.type ?? "analyze");
    const queued = job.status === "queued";
    return (
      <div
        role="status"
        className={cn(
          "mt-1.5 flex items-center gap-3 rounded-md border border-blue-border bg-blue-surface px-3.5 py-2.5",
          className,
        )}
      >
        <span aria-hidden className="size-2 shrink-0 rounded-full bg-blue" />
        <p className="min-w-0 flex-1 text-[13px] leading-[1.45] text-fg-strong">
          {state.justImported ? (
            <>
              <b className="text-fg">Imported — the transcript is ready to read.</b>{" "}
              {queued ? `${step} is waiting for a worker.` : `${step} is running (${Math.round(job.progress * 100)}%).`}{" "}
              Chapters, stats, keywords, the summary and the report fill in when it finishes; nothing on this page moves
              while they do.
            </>
          ) : (
            <>
              <b className="text-fg">
                {queued
                  ? `Queued — waiting for a worker to run ${step}.`
                  : `${step} is running (${Math.round(job.progress * 100)}%).`}
              </b>{" "}
              Chapters, stats, keywords and the summary refresh in place when it finishes; you can keep reading
              {job ? " and listening" : ""}.
            </>
          )}
        </p>
        <Button asChild variant="ghost" size="sm">
          <Link href={`/activity/${job.id}`}>View job</Link>
        </Button>
      </div>
    );
  }
  if (state.phase !== "failed") return null;
  const failed = state.failedStep;
  const spec = job?.steps[job.stepIndex];
  const label = spec ? stepLabel(spec) : failed ? stepLabel(failed) : "A step";
  const done = job ? readyBefore(job) : [];
  const partial = Boolean(job && job.steps[0]?.type !== "transcribe");
  const llm = failed === "summarize" || failed === "llm";
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {job && job.steps.length > 1 && (
        <div className="mt-1 max-w-[620px]">
          <StepLoop steps={loopSteps(job)} compact label="Pipeline steps of the last run" />
        </div>
      )}
      <div
        role="alert"
        className="mt-1.5 flex flex-wrap items-center gap-3 rounded-md border border-red-border bg-red-surface py-2.5 pl-3.5 pr-3"
      >
        <span
          aria-hidden
          className="grid size-[22px] shrink-0 place-items-center rounded-full bg-red text-[12px] font-extrabold text-white"
        >
          ✕
        </span>
        <p className="min-w-[240px] flex-1 text-[13px] leading-[1.45] text-fg-strong">
          <b className="text-fg">
            {label} failed{state.error ? ` — ${shortError(state.error)}` : ""}.
          </b>{" "}
          {job ? failureImpact(failed, done, partial, label) : "The transcript couldn't be made from the audio."}{" "}
          {job && (job.worker || job.attempts > 0) && (
            <span className="font-mono text-[12px] text-fg-secondary">
              {[job.worker, job.attempts ? `attempt ${job.attempts}` : null].filter(Boolean).join(" · ")}
            </span>
          )}
        </p>
        {job && (
          <Button asChild variant="ghost" size="sm">
            <Link href={`/activity/${job.id}`}>View logs</Link>
          </Button>
        )}
        {llm && (
          <Button
            variant="secondary"
            size="sm"
            disabled
            disabledReason="Choosing another model for one retry isn't available yet"
          >
            Retry with another model
          </Button>
        )}
        <Button
          variant="primary"
          size="sm"
          icon={<RotateCw />}
          disabled={!canEdit || retry.isPending || reprocess.isPending}
          disabledReason={!canEdit ? needRole("editor", ns) : undefined}
          onClick={() =>
            job
              ? retry.mutate(job.id)
              : reprocess.mutate({
                  steps: ["transcribe", "diarize", "analyze", "summarize", "report"],
                })
          }
        >
          Retry
        </Button>
      </div>
    </div>
  );
}
