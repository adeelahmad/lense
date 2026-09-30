"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { useRec } from "@/components/recording/context";
import { ShareEmbedDialog } from "@/components/sharing/share-dialog";
import { useEdits, useRecordingActions } from "@/components/recording/hooks";
import { orderedSteps, reprocessOptions, STEP_HELP, toggleStep, type StepKey } from "@/components/recording/jobs";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/field";
import { STEP_LABEL } from "@/components/ui/loop";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type DialogState = null | { kind: "reprocess" } | { kind: "share"; startMs?: number };

/** The page's dialogs: Reprocess (R9) and Share / Embed. */
export function RecordingDialogs({ state, onClose }: { state: DialogState; onClose: () => void }) {
  return (
    <>
      <ReprocessDialog open={state?.kind === "reprocess"} onOpenChange={(o) => !o && onClose()} />
      <ShareSlot
        open={state?.kind === "share"}
        startMs={state?.kind === "share" ? state.startMs : undefined}
        onOpenChange={(o) => !o && onClose()}
      />
    </>
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
