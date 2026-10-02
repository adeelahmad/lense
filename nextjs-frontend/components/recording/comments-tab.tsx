"use client";

import { Check, Ellipsis, Flag, MessageSquare, Pencil, Reply, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { Comment } from "@/app/openapi-client/types.gen";
import { usePlayerApi, usePlayerState } from "@/components/player/media";
import {
  TEXT_MAX,
  deleteCommentQuestion,
  newCommentBody,
  openThreads,
  resolvedBy,
  threads,
  type Thread,
} from "@/components/recording/comments-model";
import { useRec } from "@/components/recording/context";
import { useCommentActions, useComments } from "@/components/recording/hooks";
import { flagSummary } from "@/components/recording/comments-model";
import { momentLabel, writer, type NoteDraft } from "@/components/recording/notes-model";
import { Button, IconButton } from "@/components/ui/button";
import { Checkbox, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { absolute, relative, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

const chip =
  "inline-flex h-[22px] shrink-0 items-center rounded-pill border border-blue-border bg-blue-surface px-2 font-sans text-[12px] font-semibold tabular-nums text-blue-dark hover:bg-blue hover:text-white";

/** Cmd/Ctrl+Enter sends; Escape, when given, cancels. */
function sendKeys(send: () => void, cancel?: () => void) {
  return (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      send();
    }
    if (e.key === "Escape" && cancel) cancel();
  };
}

/**
 * Comments tab: a conversation everyone who can read the resource joins, about words picked in the text (its
 * Comment action), where the player is, or the whole resource; replies go on the thread. A thread is resolved by
 * whoever started it or an editor; a comment is changed only by its writer and deleted by its writer or an owner.
 */
export function CommentsTab() {
  const { id } = useRec();
  const q = useComments(id);
  const list = q.data ?? [];
  const all = threads(list);
  const open = openThreads(list);
  const resolved = all.length - open;
  const [showResolved, setShowResolved] = useState(false);
  const shown = showResolved ? all : all.filter((t) => !t.root.resolved);
  return (
    <>
      <Composer />
      {q.isLoading ? (
        <div className="flex flex-col gap-3" aria-busy>
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : q.isError ? (
        <EmptyState tone="error" icon={<MessageSquare />} title="Couldn’t load the comments">
          {q.error.message}
        </EmptyState>
      ) : all.length === 0 ? (
        <EmptyState icon={<MessageSquare />} title="No comments yet" className="py-8">
          Select words in the text and choose Comment, or write one above. Everyone who can read this sees them.
        </EmptyState>
      ) : (
        <section aria-label="Comment threads" className="flex flex-col gap-1">
          {resolved > 0 && (
            <Checkbox
              checked={showResolved}
              onCheckedChange={setShowResolved}
              label={`Show ${resolved} resolved ${resolved === 1 ? "thread" : "threads"}`}
              className="mb-1"
            />
          )}
          {shown.length === 0 && <p className="py-2 text-[13px] text-fg-muted">Every thread is resolved.</p>}
          <ul className="flex flex-col">
            {shown.map((t) => (
              <ThreadView key={t.root.id} t={t} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** Start a thread: about the text's selection (Comment), where the player is, or the whole resource. */
function Composer() {
  const { id, commentDraft, clearCommentDraft, paged, where } = useRec();
  const { hasMedia, time } = usePlayerState();
  const placed = hasMedia || paged;
  const { create } = useCommentActions(id);
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(true);
  const [moment, setMoment] = useState<NoteDraft | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // Comment in the text: about its moment and words.
  useEffect(() => {
    if (commentDraft == null) return;
    setMoment(commentDraft);
    clearCommentDraft();
    requestAnimationFrame(() => box.current?.focus());
  }, [commentDraft, clearCommentDraft]);

  const at = !moment && placed && pinned ? time : null;
  const save = () => {
    if (!text.trim() || create.isPending) return;
    create.mutate(newCommentBody(text, { draft: moment, at }), {
      onSuccess: () => {
        setText("");
        setMoment(null);
      },
    });
  };
  return (
    <form
      aria-label="New comment"
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
        <p className="text-[13px] text-fg-muted">
          About the whole recording. To comment on a moment, select its words.
        </p>
      )}
      <Textarea
        ref={box}
        aria-label="Comment"
        placeholder="Write a comment…"
        rows={3}
        maxLength={TEXT_MAX}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={sendKeys(save)}
        className="min-h-[72px] bg-background"
      />
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto text-[12px] leading-snug text-fg-muted">Everyone who can read this sees it.</span>
        <Button type="submit" size="sm" variant="primary" disabled={!text.trim() || create.isPending}>
          {create.isPending ? "Adding…" : "Comment"}
        </Button>
      </div>
    </form>
  );
}

/** One thread: its first comment, with its moment and the words it quotes, its replies, and a reply box. */
function ThreadView({ t }: { t: Thread }) {
  const { id } = useRec();
  const { create } = useCommentActions(id);
  const [replying, setReplying] = useState(false);
  const [text, setText] = useState("");
  const send = () => {
    if (!text.trim() || create.isPending) return;
    create.mutate(newCommentBody(text, { draft: null, at: null, parent: t.root.id }), {
      onSuccess: () => {
        setText("");
        setReplying(false);
      },
    });
  };
  const done = resolvedBy(t.root);
  return (
    <li
      className={cn(
        "flex flex-col gap-1.5 border-t border-border py-3 first:border-t-0",
        t.root.resolved && "opacity-80",
      )}
      aria-label={t.root.resolved ? "Resolved thread" : "Thread"}
    >
      <CommentRow c={t.root} replies={t.replies.length} />
      {done && (
        <span className="inline-flex w-fit items-center gap-1 rounded-pill border border-green-border bg-green-surface px-2 py-0.5 text-[11.5px] font-semibold text-green-dark">
          <Check className="size-3" aria-hidden /> {done}
          {t.root.resolved_at ? ` · ${relative(t.root.resolved_at)}` : ""}
        </span>
      )}
      {t.replies.length > 0 && (
        <ul className="ml-2 flex flex-col gap-2 border-l-2 border-border pl-3" aria-label="Replies">
          {t.replies.map((c) => (
            <li key={c.id}>
              <CommentRow c={c} replies={0} />
            </li>
          ))}
        </ul>
      )}
      {replying ? (
        <form
          aria-label="Reply"
          className="ml-2 flex flex-col gap-2 border-l-2 border-border pl-3"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <Textarea
            aria-label="Reply"
            placeholder="Write a reply…"
            rows={2}
            maxLength={TEXT_MAX}
            value={text}
            autoFocus
            onChange={(e) => setText(e.target.value)}
            onKeyDown={sendKeys(send, () => setReplying(false))}
            className="min-h-[56px] bg-background"
          />
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setReplying(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" variant="primary" disabled={!text.trim() || create.isPending}>
              Reply
            </Button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setReplying(true)}
          className="flex w-fit items-center gap-1 text-[12.5px] font-semibold text-fg-secondary hover:text-fg"
        >
          <Reply className="size-3.5" aria-hidden /> Reply
        </button>
      )}
    </li>
  );
}

/** One comment: its moment (plays from there), the words it quotes, its text, who wrote it, and what you may do. */
function CommentRow({ c, replies }: { c: Comment; replies: number }) {
  const { id, ns, paged, where } = useRec();
  const api = usePlayerApi();
  const { update, remove, keep } = useCommentActions(id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(c.text);
  const [confirm, setConfirm] = useState(false);
  const label = momentLabel(c, paged ? where : undefined);
  const who = ns ?? "the namespace";
  const save = () => {
    const text = draft.trim();
    if (!text) return;
    if (text === c.text) return setEditing(false);
    update.mutate({ cid: c.id, text }, { onSuccess: () => setEditing(false) });
  };
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex min-w-0 items-center gap-2">
        {label && (
          <button
            type="button"
            className={chip}
            aria-label={`Play from ${label}`}
            onClick={() => api.seek(c.t0 ?? 0, { manual: true })}
          >
            {label}
          </button>
        )}
        <span className="min-w-0 flex-1 truncate text-[12px] text-fg-muted">
          <span className="font-semibold text-fg-secondary">{writer(c)}</span> ·{" "}
          <time title={absolute(c.created_at)}>{relative(c.created_at)}</time>
          {c.edited_at ? " · edited" : ""}
        </span>
        <Menu>
          <MenuTrigger asChild>
            <button
              type="button"
              aria-label={c.parent != null ? "Reply actions" : "Comment actions"}
              className="grid size-7 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
            >
              <Ellipsis className="size-4" />
            </button>
          </MenuTrigger>
          <MenuContent align="end" className="min-w-[260px]">
            <MenuItem
              icon={<Pencil />}
              disabled={!c.mine}
              onSelect={() => {
                setDraft(c.text);
                setEditing(true);
              }}
            >
              {c.mine ? "Edit" : "Only its writer can edit it"}
            </MenuItem>
            {c.parent == null &&
              (c.resolved ? (
                <MenuItem
                  icon={<RotateCcw />}
                  disabled={!c.can_resolve}
                  onSelect={() => update.mutate({ cid: c.id, resolved: false })}
                >
                  {c.can_resolve ? "Reopen" : `Only ${writer(c)} or an editor of ${who} can reopen it`}
                </MenuItem>
              ) : (
                <MenuItem
                  icon={<Check />}
                  disabled={!c.can_resolve}
                  onSelect={() => update.mutate({ cid: c.id, resolved: true })}
                >
                  {c.can_resolve ? "Resolve" : `Only ${writer(c)} or an editor of ${who} can resolve it`}
                </MenuItem>
              ))}
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} danger disabled={!c.can_delete} onSelect={() => setConfirm(true)}>
              {c.can_delete ? "Delete…" : `Only ${writer(c)} or an owner of ${who} can delete it`}
            </MenuItem>
          </MenuContent>
        </Menu>
      </div>
      {(c.flagged?.length ?? 0) > 0 && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-gold-border bg-gold-surface px-2.5 py-1.5 text-[12.5px] text-fg"
        >
          <Flag aria-hidden className="size-3.5 shrink-0 text-gold-dark" />
          <span className="min-w-0 flex-1">
            Flagged for review: {flagSummary(c.flagged ?? [])}. Only owners see this; the comment is still shown.
          </span>
          <Button size="sm" variant="secondary" disabled={keep.isPending} onClick={() => keep.mutate(c.id)}>
            Keep it
          </Button>
        </div>
      )}
      {c.quote && (
        <blockquote className="line-clamp-4 border-l-2 border-border pl-2.5 text-[13px] italic leading-snug text-fg-secondary">
          “{c.quote}”
        </blockquote>
      )}
      {editing ? (
        <form
          aria-label="Edit comment"
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Textarea
            aria-label="Comment"
            rows={3}
            maxLength={TEXT_MAX}
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={sendKeys(save, () => setEditing(false))}
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
        <p className="whitespace-pre-wrap break-words text-[14px] leading-normal text-fg">{c.text}</p>
      )}
      {confirm && (
        <div className="flex flex-wrap items-center gap-1.5" role="alert">
          <span className="flex-1 text-[12.5px] text-fg-strong">{deleteCommentQuestion(c, replies)}</span>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            Keep
          </Button>
          <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(c.id)}>
            Delete
          </Button>
        </div>
      )}
    </div>
  );
}
