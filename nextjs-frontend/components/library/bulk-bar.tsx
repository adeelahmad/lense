"use client";

import { Download, FolderInput, RefreshCw, Tag, Trash2, X, type LucideIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { EXPORT_FORMATS, type ExportFormat } from "@/components/library/actions";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { Tooltip } from "@/components/ui/tooltip";
import { count, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

const actionCls =
  "flex h-[34px] items-center gap-1.5 rounded-pill px-3 text-[13px] font-semibold outline-offset-0 [&_svg]:size-[15px] hover:bg-[color-mix(in_srgb,var(--background)_16%,transparent)]";

function BarButton({
  icon: Icon,
  label,
  onClick,
  disabledReason,
  danger,
}: {
  icon: LucideIcon;
  label: string;
  onClick?: () => void;
  disabledReason?: ReactNode;
  danger?: boolean;
}) {
  if (disabledReason) {
    return (
      <Tooltip content={disabledReason}>
        <button
          type="button"
          aria-disabled
          className={cn(actionCls, "cursor-not-allowed opacity-50 hover:bg-transparent", danger && "text-red-border")}
          onClick={(e) => e.preventDefault()}
        >
          <Icon aria-hidden />
          {label}
        </button>
      </Tooltip>
    );
  }
  return (
    <button type="button" onClick={onClick} className={cn(actionCls, danger && "text-red-border")}>
      <Icon aria-hidden />
      {label}
    </button>
  );
}

/**
 * Library L2: the floating bulk bar. Actions you can't take in one of the selected namespaces are disabled with a count;
 * Move, Tag and Delete have no backend yet and say so.
 */
export function BulkBar({
  selected,
  blocked,
  onReprocess,
  onExport,
  onClear,
}: {
  selected: number;
  /** How many selected recordings are in namespaces where this person can't edit, and where. */
  blocked: { count: number; namespaces: string[] };
  onReprocess: () => void;
  onExport: (fmt: ExportFormat) => void;
  onClear: () => void;
}) {
  if (!selected) return null;
  const roleReason = blocked.count
    ? `You’re a viewer in ${blocked.namespaces.join(", ")}: ${count(blocked.count)} of ${count(selected)} selected can’t be changed. Ask an owner for editor access.`
    : undefined;
  return (
    <div className="pointer-events-none sticky bottom-5 z-30 mt-4 flex justify-center px-4">
      <div
        role="toolbar"
        aria-label={`${plural(selected, "recording")} selected`}
        className="pointer-events-auto flex max-w-full items-center gap-1 overflow-x-auto rounded-pill bg-fg py-1.5 pl-4 pr-1.5 text-background shadow-3 [scrollbar-width:none]"
      >
        <span className="whitespace-nowrap pr-2.5 text-[13px] font-semibold" aria-live="polite">
          {count(selected)} selected
        </span>
        <BarButton icon={RefreshCw} label="Reprocess" onClick={onReprocess} disabledReason={roleReason} />
        <BarButton
          icon={FolderInput}
          label="Move"
          disabledReason="Not available yet: recordings can’t be moved between namespaces."
        />
        <BarButton icon={Tag} label="Tag" disabledReason="Not available yet: recordings can’t be tagged." />
        <Menu>
          <MenuTrigger className={actionCls}>
            <Download aria-hidden />
            Export
          </MenuTrigger>
          <MenuContent align="center">
            <MenuLabel>Download each transcript as</MenuLabel>
            {EXPORT_FORMATS.map((f) => (
              <MenuItem key={f.value} onSelect={() => onExport(f.value)}>
                {f.label}
              </MenuItem>
            ))}
          </MenuContent>
        </Menu>
        <BarButton
          icon={Trash2}
          label="Delete"
          danger
          disabledReason="Not available yet: recordings can’t be deleted from the app."
        />
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear selection"
          className="grid size-[34px] shrink-0 place-items-center rounded-full hover:bg-[color-mix(in_srgb,var(--background)_16%,transparent)]"
        >
          <X className="size-4" />
        </button>
      </div>
    </div>
  );
}

const STEP_CHOICES = [
  {
    value: "",
    label: "Their namespace’s pipeline",
    hint: "Every step again, as set up for each namespace.",
  },
  { value: "transcribe", label: "Transcribe", hint: "Audio recordings only." },
  {
    value: "diarize",
    label: "Diarize",
    hint: "Who spoke when. Audio recordings only.",
  },
  {
    value: "analyze",
    label: "Analyze",
    hint: "Emotions, entities and chapters.",
  },
  {
    value: "summarize",
    label: "Summarize",
    hint: "Needs a language model in Settings.",
  },
  { value: "report", label: "Report", hint: "Rebuild the report pages." },
];

/** Confirm a bulk reprocess and pick what to run. */
export function ReprocessDialog({
  open,
  onOpenChange,
  n,
  withoutAudio = 0,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  n: number;
  /** How many of the selected recordings are transcript-only (Transcribe and Diarize need audio). */
  withoutAudio?: number;
  onConfirm: (steps?: string[]) => Promise<boolean>;
}) {
  const [step, setStep] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setStep("");
  }, [open]);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Reprocess ${plural(n, "recording")}?`}
      description="The work runs in the background; statuses update in the rows. Recordings already in the queue keep their current job."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              const ok = await onConfirm(step ? [step] : undefined);
              setBusy(false);
              if (ok) onOpenChange(false);
            }}
          >
            {busy ? "Queueing…" : `Reprocess ${plural(n, "recording")}`}
          </Button>
        </>
      }
    >
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-2 text-[13px] font-bold text-fg-strong">What to run</legend>
        {STEP_CHOICES.map((c) => {
          const needsAudio = c.value === "transcribe" || c.value === "diarize";
          const off = needsAudio && withoutAudio > 0;
          return (
            <label
              key={c.value}
              className={cn(
                "flex items-start gap-3 rounded-sm px-2 py-2",
                off ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-surface",
                step === c.value && "bg-hl",
              )}
            >
              <input
                type="radio"
                name="reprocess-step"
                value={c.value}
                checked={step === c.value}
                disabled={off}
                onChange={() => setStep(c.value)}
                className="mt-1 accent-[var(--aladdin-blue)]"
              />
              <span className="flex flex-col">
                <span className="text-[14px] font-semibold text-fg">{c.label}</span>
                <span className="text-[12.5px] text-fg-muted">
                  {off
                    ? `Needs audio: ${count(withoutAudio)} of ${count(n)} selected ${withoutAudio === 1 ? "is" : "are"} transcript-only.`
                    : c.hint}
                </span>
              </span>
            </label>
          );
        })}
      </fieldset>
    </Dialog>
  );
}
