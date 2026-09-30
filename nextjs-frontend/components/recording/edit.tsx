"use client";

import { ChevronDown, History, Pencil, Undo2 } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useRec } from "@/components/recording/context";
import { describeEdit, revertPatch } from "@/components/recording/edits";
import { useEdits, useRecordingActions, useSpeakerDirectory } from "@/components/recording/hooks";
import type { Segment, SpeakerInfo, Turn } from "@/components/recording/model";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { relative, tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/**
 * Transcript correction (R3). What the API supports: fix a line's text and change a line's speaker (each saved at
 * once, logged, revertable). Splitting and merging turns are not in the API, so they aren't offered.
 */

export type EditTarget = { seg: number };
type Patch = { text?: string; speaker?: number | null };
type Change = { idx: number; before: Patch };

type EditCtx = {
  target: EditTarget | null;
  setTarget: (t: EditTarget | null) => void;
  changes: Change[];
  save: (idx: number, patch: Patch, opts?: { record?: boolean }) => Promise<boolean>;
  undo: () => Promise<void>;
  saving: boolean;
};

const Ctx = createContext<EditCtx | null>(null);

export function useEdit(): EditCtx | null {
  return useContext(Ctx);
}

export function EditProvider({ children }: { children: ReactNode }) {
  const { id, model, editing } = useRec();
  const { editSegment } = useRecordingActions(id);
  const [target, setTarget] = useState<EditTarget | null>(null);
  const [changes, setChanges] = useState<Change[]>([]);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!editing) setTarget(null);
  }, [editing]);

  const save = useCallback(
    async (idx: number, patch: Patch, opts?: { record?: boolean }) => {
      const seg = model.segments[idx];
      if (!seg) return false;
      const before: Patch = {};
      if (patch.text !== undefined) {
        if (patch.text.trim() === seg.text.trim()) delete patch.text;
        else before.text = seg.text;
      }
      if (patch.speaker !== undefined) {
        const cur = seg.speaker ? Number(seg.speaker.replace(/^s/, "")) : null;
        if (patch.speaker === cur) delete patch.speaker;
        else before.speaker = cur;
      }
      if (patch.text === undefined && patch.speaker === undefined) return true;
      setSaving(true);
      try {
        await editSegment.mutateAsync({ idx, ...patch });
        if (opts?.record !== false) setChanges((c) => [...c, { idx, before }]);
        return true;
      } catch {
        return false;
      } finally {
        setSaving(false);
      }
    },
    [editSegment, model.segments],
  );

  const undo = useCallback(async () => {
    const last = changes[changes.length - 1];
    if (!last) return;
    if (await save(last.idx, { ...last.before }, { record: false })) setChanges((c) => c.slice(0, -1));
  }, [changes, save]);

  const value = useMemo(() => ({ target, setTarget, changes, save, undo, saving }), [target, changes, save, undo, saving]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The line being corrected: a serif text field; Enter saves, Esc cancels. */
export function SegmentEditor({ seg }: { seg: Segment }) {
  const edit = useEdit();
  const [text, setText] = useState(seg.text);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);
  const commit = async () => {
    if (done.current || !edit) return;
    done.current = true;
    if (text.trim() && text.trim() !== seg.text.trim()) await edit.save(seg.idx, { text: text.trim() });
    edit.setTarget(null);
  };
  return (
    <span className="block">
      <textarea
        ref={ref}
        value={text}
        rows={1}
        aria-label={`Correct the line at ${tc(seg.t0)}`}
        onChange={(e) => setText(e.target.value.replace(/\n/g, " "))}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            done.current = true;
            edit?.setTarget(null);
          }
        }}
        className="block w-full resize-none overflow-hidden rounded-xs bg-transparent font-serif text-[17.5px] leading-[1.6] text-fg outline-none"
      />
      <span className="mt-2 block font-sans text-[12px] leading-snug text-fg-muted">
        Enter saves · Esc cancels · each line keeps its timing; saving re-runs Analyze
      </span>
    </span>
  );
}

/** Reassign a turn to another speaker in this namespace (every line in the turn). */
export function ReassignMenu({ turn, current }: { turn: Turn; current: SpeakerInfo | null }) {
  const { ns } = useRec();
  const edit = useEdit();
  const dir = useSpeakerDirectory(ns);
  const toast = useToast();
  if (!edit) return null;
  const assign = async (sid: number | null, name: string) => {
    let ok = true;
    for (const idx of turn.segs) ok = (await edit.save(idx, { speaker: sid })) && ok;
    if (ok) toast({ title: `Turn at ${tc(turn.t0)} reassigned to ${name}`, tone: "green", action: { label: "Undo", onClick: () => void undoMany(edit, turn.segs.length) } });
  };
  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          type="button"
          aria-haspopup="menu"
          disabled={edit.saving}
          className="flex h-[22px] items-center gap-1 rounded-[6px] border border-border pl-2 pr-1.5 text-[11.5px] font-semibold leading-none text-fg-secondary hover:bg-surface-neutral disabled:opacity-50"
        >
          Reassign <ChevronDown className="size-3" />
        </button>
      </MenuTrigger>
      <MenuContent align="start" className="max-h-[320px] overflow-y-auto">
        <MenuLabel>Speakers in {ns}</MenuLabel>
        {dir.isLoading && <Skeleton className="m-2 w-40" />}
        {(dir.data?.speakers ?? []).map((s) => (
          <MenuItem key={s.id} onSelect={() => void assign(s.id, s.display)} shortcut={current?.id === s.id ? "current" : undefined} disabled={current?.id === s.id}>
            {s.display}
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuItem onSelect={() => void assign(null, "nobody")}>Unassigned</MenuItem>
      </MenuContent>
    </Menu>
  );
}

async function undoMany(edit: EditCtx, n: number) {
  for (let i = 0; i < n; i++) await edit.undo();
}

/** The blue edit-mode toolbar: what's happening, Undo (⌘Z), Change history, Done. */
export function EditToolbar() {
  const { setEditing } = useRec();
  const edit = useEdit();
  const n = edit?.changes.length ?? 0;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !e.shiftKey && !["INPUT", "TEXTAREA"].includes(t.tagName)) {
        e.preventDefault();
        void edit?.undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [edit]);
  return (
    <>
      <Pencil aria-hidden className="size-[15px] shrink-0 text-fg-accent" />
      <span className="whitespace-nowrap text-[13px] font-bold leading-none text-fg-accent">Editing transcript</span>
      <span className="hidden truncate text-[12px] text-fg-secondary xl:inline">
        Saves as you go · {n} {n === 1 ? "change" : "changes"} this session
      </span>
      <span role="status" className="sr-only">
        {edit?.saving ? "Saving" : ""}
      </span>
      <span className="flex-1" />
      <button
        type="button"
        onClick={() => void edit?.undo()}
        disabled={!n || edit?.saving}
        className="flex h-7 items-center gap-1.5 rounded-pill px-2.5 text-[12.5px] font-semibold text-fg-strong hover:bg-background disabled:opacity-40"
      >
        <Undo2 className="size-3.5" /> Undo <kbd className="rounded-xs border border-border px-1 py-0.5 font-sans text-[10.5px] font-medium">⌘Z</kbd>
      </button>
      <ChangeHistory />
      <Button size="sm" variant="primary" onClick={() => setEditing(false)}>
        Done
      </Button>
    </>
  );
}

/** Every saved correction to this recording's transcript, newest first, with Revert. */
export function ChangeHistoryList({ limit = 50 }: { limit?: number }) {
  const { id, ns, model, canEdit } = useRec();
  const edits = useEdits(id);
  const dir = useSpeakerDirectory(ns);
  const edit = useEdit();
  const { editSegment } = useRecordingActions(id);
  const { me } = useArchive();
  const name = (sid: number | null) => (sid == null ? "nobody" : (dir.data?.speakers.find((s) => s.id === sid)?.display ?? `Speaker ${sid}`));
  const list = edits.data ?? [];
  if (edits.isLoading) return <Skeleton className="m-1 w-48" />;
  if (!list.length) return <p className="border-t border-border px-1 py-2.5 text-[12.5px] leading-snug text-fg-muted">No corrections yet. Fixed lines and reassigned speakers are listed here, with Revert.</p>;
  return (
    <ul className="m-0 max-h-[320px] list-none overflow-y-auto p-0">
      {list.slice(0, limit).map((e, i) => {
        const seg = model.segments[e.idx];
        const patch = revertPatch(e);
        return (
          <li key={`${e.idx}-${e.at}-${i}`} className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-[3px] border-t border-border px-1 py-2">
            <span className="text-[12.5px] font-semibold leading-snug text-fg">{describeEdit(e, name)}</span>
            {canEdit && patch ? (
              <button
                type="button"
                className="self-start text-[12px] font-semibold text-blue hover:underline disabled:opacity-50"
                disabled={edit?.saving || editSegment.isPending}
                onClick={() => void (edit ? edit.save(e.idx, patch) : editSegment.mutateAsync({ idx: e.idx, ...patch }).catch(() => undefined))}
              >
                Revert
              </button>
            ) : (
              <span />
            )}
            <span className="col-span-2 text-[11.5px] leading-snug text-fg-muted">
              {seg ? tc(seg.t0) : `line ${e.idx + 1}`} · {e.by && me?.user.email === e.by ? "you" : (e.by ?? "someone")} · {relative(e.at)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function ChangeHistory() {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="flex h-7 items-center gap-1.5 rounded-pill border border-border bg-background px-2.5 text-[12.5px] font-semibold text-fg-strong hover:bg-surface-neutral">
          <History className="size-3.5" /> Change history
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[300px] p-3">
        <div className="px-1 pb-2.5 pt-1 text-[13px] font-bold">Change history</div>
        <ChangeHistoryList />
      </PopoverContent>
    </Popover>
  );
}
