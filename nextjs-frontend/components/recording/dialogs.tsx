"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { AccessFields } from "@/components/access/access-fields";
import { useRecordingAccess, useSaveAccess } from "@/components/access/hooks";
import { PeopleWithPermission } from "@/components/access/people";
import { ALL_PARTS, accessLabel, accessPatch, partsText, type AccessValue } from "@/components/access/model";
import { CopyButton } from "@/components/iiif/collections";
import { publicPath } from "@/components/public/model";
import { useRec } from "@/components/recording/context";
import { ShareEmbedDialog } from "@/components/sharing/share-dialog";
import { useEdits, useRecordingActions } from "@/components/recording/hooks";
import { orderedSteps, reprocessOptions, STEP_HELP, toggleStep, type StepKey } from "@/components/recording/jobs";
import { cleanTitle } from "@/components/recording/model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input } from "@/components/ui/field";
import { STEP_LABEL } from "@/components/ui/loop";
import { useToast } from "@/components/ui/toast";
import { Skeleton } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

export type DialogState =
  | null
  | { kind: "reprocess" }
  | { kind: "rename" }
  | { kind: "access" }
  | { kind: "share"; startMs?: number };

/** The page's dialogs: Reprocess (R9), Rename, Access and Share / Embed. */
export function RecordingDialogs({ state, onClose }: { state: DialogState; onClose: () => void }) {
  return (
    <>
      <ReprocessDialog open={state?.kind === "reprocess"} onOpenChange={(o) => !o && onClose()} />
      <RenameDialog open={state?.kind === "rename"} onOpenChange={(o) => !o && onClose()} />
      <AccessDialog open={state?.kind === "access"} onOpenChange={(o) => !o && onClose()} />
      <ShareSlot
        open={state?.kind === "share"}
        startMs={state?.kind === "share" ? state.startMs : undefined}
        onOpenChange={(o) => !o && onClose()}
      />
    </>
  );
}

/** Rename the recording (editors). Its report page follows the new title. */
export function RenameDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id, model } = useRec();
  const { rename } = useRecordingActions(id);
  const [title, setTitle] = useState(model.title);
  useEffect(() => {
    if (open) setTitle(model.title);
  }, [open, model.title]);
  const clean = cleanTitle(title);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!clean || clean === model.title) return onOpenChange(false);
    rename.mutate(clean, { onSuccess: () => onOpenChange(false) });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Rename recording"
      description="The new title shows everywhere: the Library, search, reports and shared links."
    >
      <form onSubmit={submit} className="flex flex-col gap-5">
        <Field label="Title">
          {(f) => (
            <Input
              id={f.id}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              autoFocus
              onFocus={(e) => e.currentTarget.select()}
            />
          )}
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!clean || rename.isPending}>
            {rename.isPending ? "Saving…" : "Rename"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Who may see the recording (docs/access.md). Everyone with access can look; owners change it. */
export function AccessDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id, ns } = useRec();
  const { can } = useArchive();
  const canPublish = can("owner", ns);
  const q = useRecordingAccess(id, open);
  const save = useSaveAccess(id);
  const saved: AccessValue | null = q.data
    ? { access: q.data.access, open: q.data.open, featured: q.data.featured }
    : null;
  const [draft, setDraft] = useState<AccessValue | null>(null);
  useEffect(() => {
    if (open) setDraft(null);
  }, [open]);
  const value = draft ?? saved;
  const patch = saved && value ? accessPatch(saved, value) : {};
  const dirty = Object.keys(patch).length > 0;
  const nsDefault = q.data?.default;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Who can see this recording"
      description="Members of its namespace, and people it’s shared with, always see all of it."
      wide
    >
      {!value ? (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading access">
          <Skeleton className="h-[74px] w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          <AccessFields
            value={value}
            onChange={setDraft}
            disabled={!canPublish || save.isPending}
            disabledReason={needRole("owner", ns)}
          />
          {saved?.access === "public" && <PublicPageLink id={id} />}
          {nsDefault && (
            <p className="rounded-md bg-surface px-3 py-2.5 text-[12.5px] leading-[1.45] text-fg-secondary">
              {q.data?.inherited ? "Follows the default of " : "Its own setting. The default of "}
              <b className="text-fg">{ns}</b> is {accessLabel(nsDefault.access).toLowerCase()}
              {nsDefault.access === "public"
                ? `, ${nsDefault.open.length === ALL_PARTS.length ? "everything" : partsText(nsDefault.open).toLowerCase()} open`
                : ""}
              .{" "}
              {!q.data?.inherited && canPublish && (
                <Button
                  variant="link"
                  size="xs"
                  disabled={save.isPending}
                  onClick={() => save.mutate({ access: null, open: null }, { onSuccess: () => onOpenChange(false) })}
                >
                  Use the namespace’s default
                </Button>
              )}
            </p>
          )}
          {canPublish && ns && <PeopleWithPermission rid={id} ns={ns} />}
          <div className="flex items-center justify-end gap-2">
            {!canPublish && (
              <span className="mr-auto text-[12.5px] text-fg-muted">
                Only owners of <b className="font-semibold text-fg-secondary">{ns}</b> change who can see it.
              </span>
            )}
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              {canPublish ? "Cancel" : "Close"}
            </Button>
            {canPublish && (
              <Button
                variant="primary"
                disabled={!dirty || save.isPending}
                disabledReason={!dirty ? "No changes to save" : undefined}
                onClick={() => save.mutate(patch, { onSuccess: () => onOpenChange(false) })}
              >
                {save.isPending ? "Saving…" : "Save"}
              </Button>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

/** Where visitors see a public recording: its page, to open or copy. */
function PublicPageLink({ id }: { id: number }) {
  const path = publicPath(id);
  const [url, setUrl] = useState(path);
  useEffect(() => setUrl(window.location.origin + path), [path]);
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border px-3 py-2.5">
      <b className="text-[12px] font-bold text-fg-secondary">Public page</b>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <code className="min-w-0 break-all font-mono text-[12px] text-fg">{url}</code>
        <span className="flex items-center gap-3">
          <CopyButton text={url} label="Public page link" />
          <Link
            href={path}
            target="_blank"
            className="inline-flex items-center gap-0.5 text-[12px] font-semibold text-blue hover:underline"
          >
            Open
            <ArrowUpRight aria-hidden className="size-3" />
          </Link>
        </span>
      </div>
    </div>
  );
}

/** Share / Embed (Sharing area's dialog), opened at the current time when the page asks for it. */
function ShareSlot({
  open,
  startMs,
  onOpenChange,
}: {
  open: boolean;
  startMs?: number;
  onOpenChange: (o: boolean) => void;
}) {
  const { id } = useRec();
  return <ShareEmbedDialog recordingId={id} open={open} onOpenChange={onOpenChange} startMs={startMs} />;
}

/**
 * R9: pick steps to run again. Ticking a step ticks the later steps that use its output (they can be unticked);
 * Transcribe and Diarize need audio. Starting returns at once; the header shows the step timeline.
 */
export function ReprocessDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id, model, transcriptOnly } = useRec();
  const options = useMemo(
    () =>
      reprocessOptions({
        video: model.media.kind === "video",
        hasAudio: !transcriptOnly,
      }),
    [model.media.kind, transcriptOnly],
  );
  const [selected, setSelected] = useState<Set<StepKey>>(() => new Set<StepKey>(["analyze", "summarize", "report"]));
  const edits = useEdits(id);
  const { reprocess } = useRecordingActions(id);
  const toast = useToast();
  const router = useRouter();
  const steps = orderedSteps(selected).filter((k) => options.some((o) => o.key === k && !o.disabled));
  const speakerEdits = (edits.data ?? []).filter((e) => e.after && "speaker" in e.after).length;
  const textEdits = (edits.data ?? []).filter((e) => e.after && "text" in e.after).length;
  const short = model.title.split(/\s+[—–-]\s+/)[0] || model.title;

  const run = () =>
    reprocess.mutate(
      { steps },
      {
        onSuccess: (r) => {
          onOpenChange(false);
          toast({
            title: `Reprocessing: ${steps.map((s) => STEP_LABEL[s] ?? s).join(", ")}`,
            body: "The step timeline shows progress; the job is also in Activity.",
            tone: "intent",
            action: {
              label: "View job",
              onClick: () => router.push(`/activity/${r.job}`),
            },
          });
        },
      },
    );

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Reprocess ${short}`}
      description="Pick the steps to run again. Later steps use the new output; steps you leave unticked keep what they have."
      className="max-w-[520px]"
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={run} disabled={!steps.length || reprocess.isPending}>
            {steps.length ? `Run ${steps.length} ${steps.length === 1 ? "step" : "steps"}` : "Choose a step"}
          </Button>
        </>
      }
    >
      <ul className="m-0 flex list-none flex-col rounded-md border border-border p-0" aria-label="Steps">
        {options.map((o, i) => {
          const on = selected.has(o.key) && !o.disabled;
          const row = (
            <li
              key={o.key}
              className={cn(
                "grid grid-cols-[20px_1fr] items-center gap-3 px-3.5 py-3",
                i > 0 && "border-t border-border",
                o.disabled && "opacity-60",
              )}
            >
              <Checkbox
                checked={on}
                disabled={Boolean(o.disabled)}
                onCheckedChange={(v) => setSelected((s) => toggleStep(s, o.key, v, options))}
                aria-label={STEP_LABEL[o.key] ?? o.key}
              />
              <span className="flex flex-col gap-[3px]">
                <span className="text-[14px] font-semibold leading-tight text-fg">{STEP_LABEL[o.key] ?? o.key}</span>
                <span className="text-[12.5px] leading-snug text-fg-muted">{o.disabled ?? STEP_HELP[o.key]}</span>
              </span>
            </li>
          );
          return o.disabled ? (
            <Tooltip key={o.key} content={o.disabled}>
              {row}
            </Tooltip>
          ) : (
            row
          );
        })}
      </ul>
      {selected.has("diarize") && !transcriptOnly && speakerEdits > 0 && (
        <Warn>
          Re-running <b>Diarize</b> replaces speaker turns, including your {speakerEdits} manual{" "}
          {speakerEdits === 1 ? "reassignment" : "reassignments"}.
        </Warn>
      )}
      {selected.has("transcribe") && !transcriptOnly && textEdits > 0 && (
        <Warn>
          Re-running <b>Transcribe</b> makes a new transcript: your {textEdits} corrected{" "}
          {textEdits === 1 ? "line is" : "lines are"} replaced.
        </Warn>
      )}
      {selected.has("summarize") && (
        <p className="text-[12.5px] leading-snug text-fg-muted">
          Summarize needs an AI provider; without one the step is skipped and the summary stays as it is.
        </p>
      )}
    </Dialog>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="status"
      className="flex gap-2.5 rounded-[10px] border border-gold-border bg-gold-surface px-3 py-2.5 text-[13px] leading-[1.45] text-fg-strong"
    >
      <span aria-hidden className="mt-1.5 size-2 shrink-0 rotate-45 bg-gold" />
      <span>{children}</span>
    </div>
  );
}
