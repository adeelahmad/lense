"use client";

import { Ellipsis, Lock, Pencil, StickyNote, Trash2, Users, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { Note } from "@/app/openapi-client/types.gen";
import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { useNoteActions, useNotes } from "@/components/recording/hooks";
import {
  TEXT_MAX,
  deleteQuestion,
  groupNotes,
  momentLabel,
  newNoteBody,
  noteMeta,
  writer,
  type NoteDraft,
} from "@/components/recording/notes-model";
import { Button, IconButton } from "@/components/ui/button";
import { Checkbox, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Label } from "@/components/ui/panel";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { absolute, relative, tc } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";

const SHARE_HINT = "Everyone who can read this recording sees it; only you can change it.";
const chip =
  "inline-flex h-[22px] shrink-0 items-center rounded-pill border border-blue-border bg-blue-surface px-2 font-sans text-[12px] font-semibold tabular-nums text-blue-dark hover:bg-blue hover:text-white";

/**
 * Notes tab: notes about where the player is, about words picked in the transcript (its Add note), or about the whole
 * recording. Yours stay yours; editors can share theirs with everyone who can read the recording. Only its writer
 * changes a note; its writer, or an owner of the namespace for a shared one, deletes it.
 */
export function NotesTab() {
  const { id } = useRec();
  const notes = useNotes(id);
  const list = notes.data ?? [];
  const { whole, moments } = groupNotes(list);
  return (
    <>
      <Composer />
      {notes.isLoading ? (
        <div className="flex flex-col gap-3" aria-busy>
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : notes.isError ? (
        <EmptyState tone="error" icon={<StickyNote />} title="Couldn’t load the notes">
          {notes.error.message}
        </EmptyState>
      ) : list.length === 0 ? (
        <EmptyState icon={<StickyNote />} title="No notes yet" className="py-8">
          Select words in the transcript and choose Add note, or write one above about where the player is.
        </EmptyState>
      ) : (
        <>
          {whole.length > 0 && (
            <section aria-label="About the whole recording" className="flex flex-col gap-1">
              <Label as="h3">About the whole recording</Label>
              <ul className="flex flex-col">
                {whole.map((n) => (
                  <NoteRow key={n.id} n={n} />
                ))}
              </ul>
            </section>
          )}
          {moments.length > 0 && (
            <section aria-label="By moment" className="flex flex-col gap-1">
              <Label as="h3">By moment</Label>
              <ul className="flex flex-col">
                {moments.map((n) => (
                  <NoteRow key={n.id} n={n} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </>
  );
}

/** Write a note: about the transcript's selection (Add note), where the player is, or the whole recording. */
function Composer() {
  const { id, ns, canEdit, noteDraft, clearNoteDraft, paged, where } = useRec();
  const { hasMedia, time } = usePlayerState();
  // a note can be about where the player is, or (in a document) the page with the text in view
  const placed = hasMedia || paged;
  const { create } = useNoteActions(id);
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(true);
  const [shared, setShared] = useState(false);
  const [moment, setMoment] = useState<NoteDraft | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // Add note in the transcript: write about its moment and words.
  useEffect(() => {
    if (noteDraft == null) return;
    setMoment(noteDraft);
    clearNoteDraft();
    requestAnimationFrame(() => box.current?.focus());
  }, [noteDraft, clearNoteDraft]);

  const noShare = canEdit ? undefined : needRole("editor", ns);
  const at = !moment && placed && pinned ? time : null;
  const save = () => {
    if (!text.trim() || create.isPending) return;
    create.mutate(newNoteBody(text, { draft: moment, at, shared: shared && canEdit }), {
      onSuccess: () => {
        setText("");
        setMoment(null);
        setShared(false);
      },
    });
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      save();
    }
  };
  return (
    <form
      aria-label="New note"
      className="flex flex-col gap-2.5 rounded-lg border border-border bg-surface p-3"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      {moment ? (
        <div className="flex items-start gap-2">
          <span className={chip}>{momentLabel(moment, paged ? where : undefined)}</span>
          {moment.quote ? (
            <blockquote className="line-clamp-3 min-w-0 flex-1 text-[13px] italic leading-snug text-fg-secondary">
              “{moment.quote}”
            </blockquote>
          ) : (
            <span className="flex-1" />
          )}
          <IconButton label="Not about this moment" size={24} onClick={() => setMoment(null)}>
            <X />
          </IconButton>
        </div>
      ) : placed ? (
        <Checkbox
          checked={pinned}
          onCheckedChange={setPinned}
          label={
            <span className="tabular-nums">
              {pinned
                ? paged
                  ? `On ${where(time)}, the page in view`
                  : `At ${tc(time)}, where the player is`
                : `About the whole ${paged ? "document" : "recording"}`}
            </span>
          }
        />
      ) : (
        <p className="text-[13px] text-fg-muted">About the whole recording. To note a moment, select its words.</p>
      )}
      <Textarea
        ref={box}
        aria-label="Note"
        placeholder="Write a note…"
        rows={3}
        maxLength={TEXT_MAX}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        className="min-h-[72px] bg-background"
      />
      <div className="flex flex-col gap-0.5">
        <Checkbox
          checked={shared && canEdit}
          disabled={!canEdit}
          onCheckedChange={setShared}
          label="Share with everyone who can read it"
        />
        <span className="pl-7 text-[12px] leading-snug text-fg-muted">{noShare ?? SHARE_HINT}</span>
      </div>
      <div className="flex items-center justify-end gap-2">
        {text.length > TEXT_MAX - 500 && (
          <span className="mr-auto text-[12px] tabular-nums text-fg-muted">
            {text.length} / {TEXT_MAX}
          </span>
        )}
        <Button type="submit" size="sm" variant="primary" disabled={!text.trim() || create.isPending}>
          {create.isPending ? "Adding…" : "Add note"}
        </Button>
      </div>
    </form>
  );
}

/** One note: its moment (plays from there), the words it quotes, its text, who wrote it, and what you may do. */
function NoteRow({ n }: { n: Note }) {
  const { id, ns, canEdit, paged, where } = useRec();
  const api = usePlayerApi();
  const { update, remove } = useNoteActions(id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(n.text);
  const [confirm, setConfirm] = useState(false);
  const label = momentLabel(n, paged ? where : undefined);
  const save = () => {
    const text = draft.trim();
    if (!text) return;
    if (text === n.text) return setEditing(false);
    update.mutate({ nid: n.id, text }, { onSuccess: () => setEditing(false) });
  };
  return (
    <li className="flex flex-col gap-1.5 border-t border-border py-3 first:border-t-0">
      <div className="flex min-w-0 items-center gap-2">
        {label && (
          <button
            type="button"
            className={chip}
            aria-label={`Play from ${label}`}
            onClick={() => api.seek(n.t0 ?? 0, { manual: true })}
          >
            {label}
          </button>
        )}
        <span className="flex min-w-0 flex-1 items-center gap-1 text-[12px] text-fg-muted">
          {n.shared ? (
            <Users className="size-3.5 shrink-0" aria-hidden />
          ) : (
            <Lock className="size-3.5 shrink-0" aria-hidden />
          )}
          <span className="truncate">
            {noteMeta(n)} · <time title={absolute(n.created_at)}>{relative(n.created_at)}</time>
            {n.edited_at ? " · edited" : ""}
          </span>
        </span>
        <Menu>
          <MenuTrigger asChild>
            <button
              type="button"
              aria-label="Note actions"
              className="grid size-7 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
            >
              <Ellipsis className="size-4" />
            </button>
          </MenuTrigger>
          <MenuContent align="end" className="min-w-[240px]">
            <MenuItem
              icon={<Pencil />}
              disabled={!n.mine}
              onSelect={() => {
                setDraft(n.text);
                setEditing(true);
              }}
            >
              {n.mine ? "Edit" : "Only its writer can edit it"}
            </MenuItem>
            {n.mine &&
              (n.shared ? (
                <MenuItem icon={<Lock />} onSelect={() => update.mutate({ nid: n.id, shared: false })}>
                  Make it yours only
                </MenuItem>
              ) : (
                <MenuItem
                  icon={<Users />}
                  disabled={!canEdit}
                  onSelect={() => update.mutate({ nid: n.id, shared: true })}
                >
                  {canEdit ? "Share with everyone who can read it" : needRole("editor", ns)}
                </MenuItem>
              ))}
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} danger disabled={!n.can_delete} onSelect={() => setConfirm(true)}>
              {n.can_delete ? "Delete…" : `Only ${writer(n)} or an owner of ${ns ?? "the namespace"} can delete it`}
            </MenuItem>
          </MenuContent>
        </Menu>
      </div>
      {n.quote && (
        <blockquote className="line-clamp-4 border-l-2 border-border pl-2.5 text-[13px] italic leading-snug text-fg-secondary">
          “{n.quote}”
        </blockquote>
      )}
      {editing ? (
        <form
          aria-label="Edit note"
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Textarea
            aria-label="Note"
            rows={3}
            maxLength={TEXT_MAX}
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                save();
              }
              if (e.key === "Escape") setEditing(false);
            }}
            className="min-h-[72px]"
          />
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" variant="primary" disabled={!draft.trim() || update.isPending}>
              Save
            </Button>
          </div>
        </form>
      ) : (
        <p className="whitespace-pre-wrap break-words text-[14px] leading-normal text-fg">{n.text}</p>
      )}
      {confirm && (
        <div className="flex flex-wrap items-center gap-1.5" role="alert">
          <span className="flex-1 text-[12.5px] text-fg-strong">{deleteQuestion(n)}</span>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            Keep
          </Button>
          <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(n.id)}>
            Delete
          </Button>
        </div>
      )}
    </li>
  );
}
