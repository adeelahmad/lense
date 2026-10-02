"use client";

import { Download, FolderInput, FolderTree, RefreshCw, Tag, Trash2, X, type LucideIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { EXPORT_FORMATS, type ExportFormat } from "@/components/library/actions";
import { hasSound, tagsFromText, tagsOn } from "@/components/library/model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select } from "@/components/ui/field";
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

/** Library L2: the floating bulk bar. Actions you can't take in one of the selected namespaces are disabled with a count. */
export function BulkBar({
  selected,
  blocked,
  notOwner,
  moveTargets,
  onReprocess,
  onExport,
  onMove,
  onPlace,
  placeReason,
  onTag,
  onDelete,
  onClear,
}: {
  selected: number;
  /** How many selected recordings are in namespaces where this person can't edit, and where. */
  blocked: { count: number; namespaces: string[] };
  /** The same for owning: only owners move and delete. */
  notOwner: { count: number; namespaces: string[] };
  /** Namespaces the selection can move to. */
  moveTargets: string[];
  onReprocess: () => void;
  onExport: (fmt: ExportFormat) => void;
  onMove: () => void;
  /** File them in a collection of their namespace. */
  onPlace?: () => void;
  /** Why they can't be (besides roles): they're from several namespaces. */
  placeReason?: string;
  onTag: () => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  if (!selected) return null;
  const roleReason = blocked.count
    ? `You’re a viewer in ${blocked.namespaces.join(", ")}: ${count(blocked.count)} of ${count(selected)} selected can’t be changed. Ask an owner for editor access.`
    : undefined;
  const deleteReason = notOwner.count
    ? `Only owners delete recordings. You don’t own ${notOwner.namespaces.join(", ")}: ${count(notOwner.count)} of ${count(selected)} selected can’t be deleted.`
    : undefined;
  const moveReason = notOwner.count
    ? `Only owners move recordings out of their namespace. You don’t own ${notOwner.namespaces.join(", ")}: ${count(notOwner.count)} of ${count(selected)} selected can’t be moved.`
    : !moveTargets.length
      ? "There’s no other namespace you edit to move them to."
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
        <BarButton icon={FolderInput} label="Move" onClick={onMove} disabledReason={moveReason} />
        {onPlace && (
          <BarButton
            icon={FolderTree}
            label="Collection"
            onClick={onPlace}
            disabledReason={roleReason ?? placeReason}
          />
        )}
        <BarButton icon={Tag} label="Tag" onClick={onTag} disabledReason={roleReason} />
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
        <BarButton icon={Trash2} label="Delete" danger onClick={onDelete} disabledReason={deleteReason} />
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
    value: "embed",
    label: "Index for search by meaning",
    hint: "Needs an embedding model in Settings → Search.",
  },
  {
    value: "classify",
    label: "Suggest tags and collections",
    hint: "Needs a decision model in Settings → Decisions.",
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

/** Confirm deleting recordings (owners): what goes with them, and that their media files stay. */
export function DeleteDialog({
  open,
  onOpenChange,
  rows,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  rows: readonly { id: number; title?: string | null }[];
  /** Deletes them, reporting how many are done so far. */
  onConfirm: (onProgress: (n: number) => void) => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  useEffect(() => {
    if (open) {
      setBusy(false);
      setDone(0);
    }
  }, [open]);
  const n = rows.length;
  const name = (r: { id: number; title?: string | null }) => r.title || `Recording ${r.id}`;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !busy && onOpenChange(o)}
      title={n === 1 ? `Delete “${name(rows[0])}”?` : `Delete ${plural(n, "recording")}?`}
      description="Their transcripts and analysis, chapters, reports and outputs, shares and permissions go with them. This can’t be undone."
      actions={
        <>
          <Button variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={busy || !n}
            onClick={async () => {
              setBusy(true);
              await onConfirm(setDone);
              setBusy(false);
              onOpenChange(false);
            }}
          >
            {busy
              ? `Deleting… ${count(done)} of ${count(n)}`
              : n === 1
                ? "Delete recording"
                : `Delete ${plural(n, "recording")}`}
          </Button>
        </>
      }
    >
      {n > 1 && (
        <ul aria-label="Recordings to delete" className="flex flex-col gap-1 text-[13.5px] text-fg">
          {rows.slice(0, 5).map((r) => (
            <li key={r.id} className="truncate">
              {name(r)}
            </li>
          ))}
          {n > 5 && <li className="text-fg-muted">and {plural(n - 5, "more", "more")}</li>}
        </ul>
      )}
      <p className="rounded-md bg-surface px-3 py-2.5 text-[12.5px] leading-[1.45] text-fg-secondary">
        The media files stay where they are, and scans and watched folders won’t import them again. Importing one on
        purpose brings it back.
      </p>
    </Dialog>
  );
}

type MoveRow = { id: number; title?: string | null; namespace?: string | null; media_kind?: string | null };

/**
 * Move recordings to another namespace (owners where they are, editors there). Their access and IIIF stay as they
 * were; speakers are matched by name there, or identified again from their voices; share links keep working unless
 * revoked (docs/api.md).
 */
export function MoveDialog({
  open,
  onOpenChange,
  rows,
  targets,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  rows: readonly MoveRow[];
  targets: readonly string[];
  /** Moves them, reporting how many are done so far. */
  onConfirm: (
    rows: readonly MoveRow[],
    to: string,
    opts: { rediarize: boolean; revokeShares: boolean },
    onProgress: (n: number) => void,
  ) => Promise<unknown>;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [rediarize, setRediarize] = useState(false);
  const [revokeShares, setRevokeShares] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  useEffect(() => {
    if (open) {
      setPicked(null);
      setRediarize(false);
      setRevokeShares(false);
      setBusy(false);
      setDone(0);
    }
  }, [open]);
  const to = picked && targets.includes(picked) ? picked : (targets[0] ?? "");
  const moving = rows.filter((r) => r.namespace !== to);
  const already = rows.length - moving.length;
  const withAudio = moving.filter(hasSound).length;
  const from = [...new Set(moving.map((r) => r.namespace ?? "?"))].join(", ");
  const n = moving.length;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !busy && onOpenChange(o)}
      title={
        rows.length === 1
          ? `Move “${rows[0].title || `Recording ${rows[0].id}`}”`
          : `Move ${plural(rows.length, "recording")}`
      }
      description="Their transcripts, media, outputs and permissions go with them."
      actions={
        <>
          <Button variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy || !n || !to}
            disabledReason={!n ? `They’re already in ${to}` : undefined}
            onClick={async () => {
              setBusy(true);
              await onConfirm(moving, to, { rediarize, revokeShares }, setDone);
              setBusy(false);
              onOpenChange(false);
            }}
          >
            {busy ? `Moving… ${count(done)} of ${count(n)}` : `Move ${plural(n, "recording")}`}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field
          label="To"
          hint={already ? `${plural(already, "recording")} already there stay as they are.` : undefined}
        >
          {(f) => (
            <Select
              id={f.id}
              aria-describedby={f.describedBy}
              value={to}
              onChange={(e) => setPicked(e.target.value)}
              options={targets.map((t) => ({ value: t, label: t }))}
            />
          )}
        </Field>
        <Tooltip content={!withAudio ? "Only recordings with audio have voices to identify" : undefined}>
          <span className="w-fit">
            <Checkbox
              checked={rediarize && withAudio > 0}
              disabled={!withAudio || busy}
              onCheckedChange={setRediarize}
              label={
                <span className="flex flex-col">
                  <span className="text-[13.5px] font-semibold text-fg">Identify speakers again from their voices</span>
                  <span className="text-[12px] text-fg-muted">
                    Against {to || "the new namespace"}’s voiceprints. Otherwise speakers are matched by name.
                  </span>
                </span>
              }
            />
          </span>
        </Tooltip>
        <Checkbox
          checked={revokeShares}
          disabled={busy}
          onCheckedChange={setRevokeShares}
          label={
            <span className="flex flex-col">
              <span className="text-[13.5px] font-semibold text-fg">Stop their share links working</span>
              <span className="text-[12px] text-fg-muted">Otherwise links already sent keep working.</span>
            </span>
          }
        />
        <p className="rounded-md bg-surface px-3 py-2.5 text-[12.5px] leading-[1.45] text-fg-secondary">
          Their access and IIIF manifests stay as they are: what they had from {from || "their namespace"} is kept on
          each recording. Analysis runs again in {to || "the new namespace"} to find their entities, and{" "}
          {from || "their namespace"}’s scans and watched folders won’t import them again.
        </p>
      </div>
    </Dialog>
  );
}

/** Add tags to the selected recordings, and take off tags they have (editors). */
export function TagDialog({
  open,
  onOpenChange,
  rows,
  known,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  rows: readonly { id: number; tags?: string[] | null }[];
  /** Tags already in use, to suggest. */
  known: readonly string[];
  onConfirm: (add: string[], remove: string[]) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [off, setOff] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) {
      setText("");
      setOff([]);
      setBusy(false);
    }
  }, [open]);
  const add = tagsFromText(text);
  const have = tagsOn(rows);
  const isOff = (t: string) => off.some((x) => x.toLowerCase() === t.toLowerCase());
  const n = rows.length;
  const nothing = !add.length && !off.length;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !busy && onOpenChange(o)}
      title={`Tag ${plural(n, "recording")}`}
      description="Tags help find recordings in the Library: filter by them, or see them in the Tags column."
      actions={
        <>
          <Button variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy || nothing}
            disabledReason={nothing ? "Add a tag, or pick one to take off" : undefined}
            onClick={async () => {
              setBusy(true);
              const ok = await onConfirm(add, off);
              setBusy(false);
              if (ok) onOpenChange(false);
            }}
          >
            {busy ? "Saving…" : "Save tags"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Add" hint="Separate tags with commas">
          {(f) => (
            <>
              <Input
                id={f.id}
                aria-describedby={f.describedBy}
                list={`${f.id}-known`}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="board, Q3 review"
                autoComplete="off"
              />
              <datalist id={`${f.id}-known`}>
                {known.slice(0, 50).map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </>
          )}
        </Field>
        {have.length > 0 && (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[13px] font-bold text-fg-strong">Take off</legend>
            <div className="flex flex-wrap gap-1.5">
              {have.map((t) => (
                <button
                  key={t.tag}
                  type="button"
                  aria-pressed={isOff(t.tag)}
                  onClick={() =>
                    setOff(isOff(t.tag) ? off.filter((x) => x.toLowerCase() !== t.tag.toLowerCase()) : [...off, t.tag])
                  }
                  className={cn(
                    "inline-flex h-7 items-center gap-1 rounded-pill border px-2.5 text-[12.5px] font-medium transition-colors duration-fast",
                    isOff(t.tag)
                      ? "border-red-border bg-red-surface text-red-dark line-through"
                      : "border-border bg-surface-neutral text-fg-secondary hover:bg-surface",
                  )}
                >
                  {t.tag}
                  <span className="text-fg-muted">{n > 1 ? `${t.count}/${n}` : ""}</span>
                  <X aria-hidden className="size-3" />
                </button>
              ))}
            </div>
          </fieldset>
        )}
      </div>
    </Dialog>
  );
}
