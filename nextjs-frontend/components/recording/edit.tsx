"use client";

import { ChevronDown, History, Merge, Pencil, Scissors, Undo2 } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";

import { useRec } from "@/components/recording/context";
import { describeEdit, revertPatch } from "@/components/recording/edits";
import { useEdits, useRecordingActions, useSpeakerDirectory } from "@/components/recording/hooks";
import { joinedAt, splitPoint, type Segment, type SpeakerInfo, type Turn } from "@/components/recording/model";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { relative, tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/**
 * Transcript correction (R3): fix a line's text, change a line's speaker, split a line (optionally giving the rest to
 * another speaker) and join it with the next one. Each is saved at once and logged; Undo takes back this session's
 * changes in reverse order.
 */

export type EditTarget = { seg: number };
type Patch = { text?: string; speaker?: number | null };
type Change =
  | { kind: "edit"; idx: number; before: Patch }
  | { kind: "split"; idx: number }
  | { kind: "merge"; idx: number; at: number; t: number; speaker?: number | null };

type EditCtx = {
  target: EditTarget | null;
  setTarget: (t: EditTarget | null) => void;
  changes: Change[];
  save: (idx: number, patch: Patch, opts?: { record?: boolean }) => Promise<boolean>;
  /** Split line `idx` at `at` in its text; `speaker` (an id, or null) says who says the rest. */
  split: (idx: number, at: number, speaker?: number | null) => Promise<boolean>;
  /** Join line `idx` with the next one. */
  merge: (idx: number) => Promise<boolean>;
  undo: () => Promise<void>;
  saving: boolean;
};

const speakerId = (s: Segment | undefined) => (s?.speaker ? Number(s.speaker.replace(/^s/, "")) : null);

const Ctx = createContext<EditCtx | null>(null);

export function useEdit(): EditCtx | null {
  return useContext(Ctx);
}

export function EditProvider({ children }: { children: ReactNode }) {
  const { id, model, editing } = useRec();
  const { editSegment, splitSegment, mergeSegments } = useRecordingActions(id);
  const toast = useToast();
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
        if (opts?.record !== false) setChanges((c) => [...c, { kind: "edit", idx, before }]);
        return true;
      } catch {
        return false;
      } finally {
        setSaving(false);
      }
    },
    [editSegment, model.segments],
  );

  /** Run a split or join, and keep it for Undo when `change` is given. */
  const run = useCallback(
    async (fn: () => Promise<unknown>, change?: Change, done?: string) => {
      setSaving(true);
      try {
        await fn();
        if (change) setChanges((c) => [...c, change]);
        if (done) toast({ title: done, tone: "green" });
        return true;
      } catch {
        return false;
      } finally {
        setSaving(false);
      }
    },
    [toast],
  );

  const split = useCallback(
    (idx: number, at: number, speaker?: number | null) =>
      run(
        () => splitSegment.mutateAsync({ idx, at, ...(speaker !== undefined ? { speaker } : {}) }),
        { kind: "split", idx },
        `Line at ${tc(model.segments[idx]?.t0 ?? 0)} split`,
      ),
    [run, splitSegment, model.segments],
  );

  const merge = useCallback(
    async (idx: number) => {
      const a = model.segments[idx];
      const b = model.segments[idx + 1];
      if (!a || !b) return false;
      const back: Change = { kind: "merge", idx, at: joinedAt(a.text, b.text), t: b.t0 };
      if (speakerId(a) !== speakerId(b)) back.speaker = speakerId(b);
      return run(() => mergeSegments.mutateAsync(idx), back, `Lines at ${tc(a.t0)} joined`);
    },
    [run, mergeSegments, model.segments],
  );

  const undo = useCallback(async () => {
    const last = changes[changes.length - 1];
    if (!last) return;
    const ok =
      last.kind === "edit"
        ? await save(last.idx, { ...last.before }, { record: false })
        : last.kind === "split"
          ? await run(() => mergeSegments.mutateAsync(last.idx))
          : await run(() =>
              splitSegment.mutateAsync({
                idx: last.idx,
                at: last.at,
                t: last.t,
                ...(last.speaker !== undefined ? { speaker: last.speaker } : {}),
              }),
            );
    if (ok) setChanges((c) => c.slice(0, -1));
  }, [changes, save, run, mergeSegments, splitSegment]);

  const value = useMemo(
    () => ({ target, setTarget, changes, save, split, merge, undo, saving }),
    [target, changes, save, split, merge, undo, saving],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * The line being corrected: a serif text field; Enter saves, Esc cancels. Shift+Enter (or Split here) splits it at the
 * cursor, optionally giving the rest to another speaker; Join with next line (or Delete at its end) joins the next one,
 * Backspace at its start the one above. Leaving the field saves.
 */
export function SegmentEditor({ seg }: { seg: Segment }) {
  const { model, ns } = useRec();
  const edit = useEdit();
  const dir = useSpeakerDirectory(ns);
  const toast = useToast();
  const [text, setText] = useState(seg.text);
  const [picking, setPicking] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  const last = seg.idx >= model.segments.length - 1;
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
  /** Save the text if it changed, then split or join, and close the field. */
  const then = async (fn: (e: EditCtx) => Promise<boolean>) => {
    if (done.current || !edit) return;
    done.current = true;
    const value = text.trim();
    if (value && value !== seg.text.trim() && !(await edit.save(seg.idx, { text: value }))) {
      done.current = false;
      return;
    }
    await fn(edit);
    edit.setTarget(null);
  };
  const split = (speaker?: number | null) => {
    const lead = text.length - text.trimStart().length;
    const point = splitPoint(text.trim(), (ref.current?.selectionStart ?? 0) - lead);
    if (!point) {
      toast({ title: "Put the cursor between two words to split the line", tone: "red" });
      return;
    }
    void then((e) => e.split(seg.idx, point.at, speaker));
  };
  const tool =
    "inline-flex h-7 items-center gap-1.5 rounded-pill border border-border bg-background px-2.5 text-[12px] font-semibold text-fg-strong hover:bg-surface-neutral disabled:opacity-40 [&_svg]:size-3.5";
  const keep = (e: MouseEvent) => e.preventDefault(); // the field keeps its focus and cursor
  return (
    <span
      className="block"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) void commit();
      }}
    >
      <textarea
        ref={ref}
        value={text}
        rows={1}
        aria-label={`Correct the line at ${tc(seg.t0)}`}
        onChange={(e) => setText(e.target.value.replace(/\n/g, " "))}
        onKeyDown={(e) => {
          const el = e.currentTarget;
          const caret = el.selectionStart === el.selectionEnd ? el.selectionStart : -1;
          if (e.key === "Enter" && e.shiftKey) {
            e.preventDefault();
            split();
          } else if (e.key === "Enter") {
            e.preventDefault();
            void commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            done.current = true;
            edit?.setTarget(null);
          } else if (e.key === "Backspace" && caret === 0 && seg.idx > 0) {
            e.preventDefault();
            void then((x) => x.merge(seg.idx - 1));
          } else if (e.key === "Delete" && caret === el.value.length && !last) {
            e.preventDefault();
            void then((x) => x.merge(seg.idx));
          }
        }}
        className="block w-full resize-none overflow-hidden rounded-xs bg-transparent font-serif text-[17.5px] leading-[1.6] text-fg outline-none"
      />
      <span className="mt-2 flex flex-wrap items-center gap-1.5 font-sans">
        <button type="button" className={tool} onMouseDown={keep} onClick={() => split()} disabled={edit?.saving}>
          <Scissors aria-hidden /> Split here
        </button>
        <button
          type="button"
          className={tool}
          onMouseDown={keep}
          onClick={() => setPicking((v) => !v)}
          aria-expanded={picking}
          disabled={edit?.saving}
        >
          Split, the rest is… <ChevronDown aria-hidden />
        </button>
        <button
          type="button"
          className={tool}
          onMouseDown={keep}
          onClick={() => void then((x) => x.merge(seg.idx))}
          disabled={last || edit?.saving}
          title={last ? "This is the last line" : undefined}
        >
          <Merge aria-hidden /> Join with next line
        </button>
      </span>
      {picking && (
        <span role="group" aria-label="Who says the rest" className="mt-1.5 flex flex-wrap gap-1 font-sans">
          {(dir.data?.speakers ?? []).map((s) => (
            <button key={s.id} type="button" className={tool} onMouseDown={keep} onClick={() => split(s.id)}>
              {s.display}
            </button>
          ))}
          <button type="button" className={tool} onMouseDown={keep} onClick={() => split(null)}>
            Nobody yet
          </button>
        </span>
      )}
      <span className="mt-2 block font-sans text-[12px] leading-snug text-fg-muted">
        Enter saves · Shift+Enter splits at the cursor · Backspace at the start joins the line above · Esc cancels;
        saving re-runs Analyze
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
    if (ok)
      toast({
        title: `Turn at ${tc(turn.t0)} reassigned to ${name}`,
        tone: "green",
        action: {
          label: "Undo",
          onClick: () => void undoMany(edit, turn.segs.length),
        },
      });
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
          <MenuItem
            key={s.id}
            onSelect={() => void assign(s.id, s.display)}
            shortcut={current?.id === s.id ? "current" : undefined}
            disabled={current?.id === s.id}
          >
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
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key.toLowerCase() === "z" &&
        !e.shiftKey &&
        !["INPUT", "TEXTAREA"].includes(t.tagName)
      ) {
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
        <Undo2 className="size-3.5" /> Undo{" "}
        <kbd className="rounded-xs border border-border px-1 py-0.5 font-sans text-[10.5px] font-medium">⌘Z</kbd>
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
  const { id, ns, model, canEdit, where } = useRec();
  const edits = useEdits(id);
  const dir = useSpeakerDirectory(ns);
  const edit = useEdit();
  const { editSegment } = useRecordingActions(id);
  const { me } = useArchive();
  const name = (sid: number | null) =>
    sid == null ? "nobody" : (dir.data?.speakers.find((s) => s.id === sid)?.display ?? `Speaker ${sid}`);
  const list = edits.data ?? [];
  if (edits.isLoading) return <Skeleton className="m-1 w-48" />;
  if (!list.length)
    return (
      <p className="border-t border-border px-1 py-2.5 text-[12.5px] leading-snug text-fg-muted">
        No corrections yet. Fixed lines and reassigned speakers are listed here, with Revert.
      </p>
    );
  return (
    <ul className="m-0 max-h-[320px] list-none overflow-y-auto p-0">
      {list.slice(0, limit).map((e, i) => {
        const seg = model.segments[e.idx];
        const patch = revertPatch(e);
        return (
          <li
            key={`${e.idx}-${e.at}-${i}`}
            className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-[3px] border-t border-border px-1 py-2"
          >
            <span className="text-[12.5px] font-semibold leading-snug text-fg">{describeEdit(e, name)}</span>
            {canEdit && patch ? (
              <button
                type="button"
                className="self-start text-[12px] font-semibold text-blue hover:underline disabled:opacity-50"
                disabled={edit?.saving || editSegment.isPending}
                onClick={() =>
                  void (edit
                    ? edit.save(e.idx, patch)
                    : editSegment.mutateAsync({ idx: e.idx, ...patch }).catch(() => undefined))
                }
              >
                Revert
              </button>
            ) : (
              <span />
            )}
            <span className="col-span-2 text-[11.5px] leading-snug text-fg-muted">
              {seg ? where(seg.t0) : `line ${e.idx + 1}`} ·{" "}
              {e.by && me?.user.email === e.by ? "you" : (e.by ?? "someone")} · {relative(e.at)}
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
        <button
          type="button"
          className="flex h-7 items-center gap-1.5 rounded-pill border border-border bg-background px-2.5 text-[12.5px] font-semibold text-fg-strong hover:bg-surface-neutral"
        >
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
