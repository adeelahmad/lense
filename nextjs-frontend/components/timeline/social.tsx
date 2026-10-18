"use client";

import { MessageSquare, Reply, SmilePlus, Sparkles, Trash2 } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import type { Comment, Reaction } from "@/app/openapi-client/types.gen";
import { threads, type Thread } from "@/components/recording/comments-model";
import { writer } from "@/components/recording/notes-model";
import type { Activity } from "@/components/timeline/model";
import { EMOJIS, EMOJI_LABEL, commentsLabel, linked } from "@/components/timeline/social-model";
import { useActivityCommentActions, useActivityComments } from "@/components/timeline/use-social";
import { Button, IconButton } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";

const chip = "inline-flex h-6 items-center gap-1 rounded-pill border px-2 text-[12px] tabular-nums transition-colors";

/** The reactions on an activity, each a chip that gives or takes back yours, and a picker for the rest. */
export function ReactionBar({
  reactions,
  onReact,
  className,
}: {
  reactions: Reaction[];
  onReact?: (emoji: string, on: boolean) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className={cn("flex flex-wrap items-center gap-1", className)} data-testid="reactions">
      {reactions.map((r) => (
        <button
          key={r.emoji}
          type="button"
          aria-pressed={r.mine}
          aria-label={`${EMOJI_LABEL[r.emoji] ?? r.emoji} ${r.count}${r.mine ? ", yours" : ""}`}
          title={r.mine ? "Take yours back" : "React too"}
          disabled={!onReact}
          onClick={(e) => {
            e.stopPropagation();
            onReact?.(r.emoji, !r.mine);
          }}
          className={cn(
            chip,
            r.mine
              ? "border-blue-border bg-blue-surface text-blue-dark"
              : "border-border bg-surface text-fg-secondary hover:bg-surface-neutral",
          )}
        >
          <span aria-hidden>{r.emoji}</span>
          {r.count}
        </button>
      ))}
      {onReact && (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger
            aria-label="React"
            title="React"
            onClick={(e) => e.stopPropagation()}
            className="grid size-6 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
          >
            <SmilePlus className="size-4" />
          </PopoverTrigger>
          <PopoverContent className="flex gap-0.5 p-1">
            {EMOJIS.map((e) => {
              const mine = reactions.some((r) => r.emoji === e && r.mine);
              return (
                <button
                  key={e}
                  type="button"
                  aria-label={EMOJI_LABEL[e]}
                  aria-pressed={mine}
                  onClick={() => {
                    onReact(e, !mine);
                    setOpen(false);
                  }}
                  className={cn(
                    "grid size-8 place-items-center rounded-md text-[18px] hover:bg-surface-neutral",
                    mine && "bg-blue-surface",
                  )}
                >
                  {e}
                </button>
              );
            })}
          </PopoverContent>
        </Popover>
      )}
    </span>
  );
}

/** What a closed line shows of its items' reactions and comments: the chips, read-only, and "3 comments". */
export function SocialSummary({ reactions, comments }: { reactions: Reaction[]; comments: number }) {
  if (!reactions.length && !comments) return null;
  return (
    <span className="flex flex-wrap items-center gap-1.5 pl-7 text-[12px] text-fg-muted">
      {reactions.map((r) => (
        <span
          key={r.emoji}
          className={cn(chip, r.mine ? "border-blue-border bg-blue-surface text-blue-dark" : "border-border")}
        >
          <span aria-hidden>{r.emoji}</span>
          {r.count}
        </span>
      ))}
      {comments > 0 && (
        <span className="inline-flex items-center gap-1">
          <MessageSquare aria-hidden className="size-3.5" />
          {commentsLabel(comments)}
        </span>
      )}
    </span>
  );
}

/** Enter sends; Shift+Enter makes a new line; Escape, when given, cancels. */
function sendKeys(send: () => void, cancel?: () => void) {
  return (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
    if (e.key === "Escape" && cancel) cancel();
  };
}

function Composer({
  label,
  placeholder,
  pending,
  autoFocus,
  onSend,
  onCancel,
}: {
  label: string;
  placeholder: string;
  pending: boolean;
  autoFocus?: boolean;
  onSend: (text: string) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const [text, setText] = useState("");
  const send = () => {
    if (!text.trim() || pending) return;
    void onSend(text.trim()).then(() => setText(""));
  };
  return (
    <form
      aria-label={label}
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <Textarea
        aria-label={label}
        rows={1}
        autoFocus={autoFocus}
        value={text}
        placeholder={placeholder}
        maxLength={5000}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={sendKeys(send, onCancel)}
        className="min-h-9 flex-1 py-1.5 text-[13px]"
      />
      <Button type="submit" size="xs" variant="secondary" disabled={!text.trim() || pending}>
        {label}
      </Button>
    </form>
  );
}

function CommentLine({
  c,
  replies,
  onReply,
  onDelete,
}: {
  c: Comment;
  replies: number;
  onReply?: () => void;
  onDelete: () => void;
}) {
  return (
    <li className={cn("flex flex-col gap-0.5 py-1", c.parent != null && "ml-5 border-l border-border pl-3")}>
      <span className="flex items-center gap-2 text-[12px] text-fg-muted">
        <span className="inline-flex items-center gap-1 font-semibold text-fg">
          {c.by_agent && <Sparkles className="size-3 text-fg-accent" aria-label="the agent" />}
          {writer(c)}
        </span>
        <span title={c.created_at ?? undefined}>{relative(c.created_at)}</span>
        {c.edited_at && <span>· edited</span>}
        <span className="ml-auto flex items-center gap-0.5">
          {onReply && (
            <IconButton label="Reply" size={24} onClick={onReply}>
              <Reply className="size-3.5" />
            </IconButton>
          )}
          {c.can_delete && (
            <IconButton
              label={replies ? `Delete, with its ${replies === 1 ? "reply" : `${replies} replies`}` : "Delete"}
              size={24}
              onClick={onDelete}
            >
              <Trash2 className="size-3.5" />
            </IconButton>
          )}
        </span>
      </span>
      <p className="whitespace-pre-wrap text-[13px] text-fg">
        {c.by_agent
          ? linked(c.text).map((part, i) =>
              part.url ? (
                <a key={i} href={part.url} className="text-fg-accent underline-offset-2 hover:underline">
                  {part.text}
                </a>
              ) : (
                <span key={i}>{part.text}</span>
              ),
            )
          : c.text}
      </p>
    </li>
  );
}

function ThreadView({
  t,
  onReply,
  onDelete,
  replying,
  setReplying,
  pending,
}: {
  t: Thread;
  onReply: (text: string, parent: number) => Promise<unknown>;
  onDelete: (c: Comment, replies: number) => void;
  replying: boolean;
  setReplying: (on: boolean) => void;
  pending: boolean;
}) {
  return (
    <li className="flex flex-col">
      <ul className="flex flex-col">
        <CommentLine
          c={t.root}
          replies={t.replies.length}
          onReply={() => setReplying(true)}
          onDelete={() => onDelete(t.root, t.replies.length)}
        />
        {t.replies.map((r) => (
          <CommentLine key={r.id} c={r} replies={0} onDelete={() => onDelete(r, 0)} />
        ))}
      </ul>
      {replying && (
        <div className="ml-5 pl-3">
          <Composer
            label="Reply"
            placeholder="Reply… (Enter sends)"
            pending={pending}
            autoFocus
            onSend={(text) => onReply(text, t.root.id).then(() => setReplying(false))}
            onCancel={() => setReplying(false)}
          />
        </div>
      )}
    </li>
  );
}

/** The conversation on an activity: its threads, with replies, and a box to start one. Everyone who sees the
 * activity reads and joins it; on an asset's arrival it is the asset's own comments. */
export function CommentThread({ a, bump }: { a: Activity; bump: (id: string, by: number) => void }) {
  const q = useActivityComments(a.id, true);
  const { create, remove } = useActivityCommentActions(a.id, bump);
  const [replying, setReplying] = useState<number | null>(null);
  const list = q.data ?? [];
  const all = threads(list);
  const pending = create.isPending;
  const send = (text: string, parent?: number) => create.mutateAsync({ text, parent: parent ?? null });
  return (
    <div className="flex flex-col gap-1.5" aria-label="Comments">
      {q.isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : q.isError ? (
        <p className="text-[12.5px] text-red-dark">Couldn’t load the comments: {q.error.message}</p>
      ) : (
        all.length > 0 && (
          <ul className="flex flex-col">
            {all.map((t) => (
              <ThreadView
                key={t.root.id}
                t={t}
                onReply={send}
                onDelete={(c, replies) => remove.mutate({ cid: c.id, replies })}
                replying={replying === t.root.id}
                setReplying={(on) => setReplying(on ? t.root.id : null)}
                pending={pending}
              />
            ))}
          </ul>
        )
      )}
      <Composer
        label="Comment"
        placeholder={
          all.length
            ? "Add a comment, or ask @lens… (Enter sends)"
            : "Comment, or ask @lens… everyone who sees this reads it (Enter sends)"
        }
        pending={pending}
        onSend={(text) => send(text)}
      />
    </div>
  );
}

/** An opened item's reactions and comments: the bar with its picker, and the thread, opened with "3 comments" or
 * the comment button. */
export function Social({
  a,
  reactions,
  onReact,
  comments,
  bump,
}: {
  a: Activity;
  reactions: Reaction[];
  onReact: (emoji: string, on: boolean) => void;
  comments: number;
  bump: (id: string, by: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2" data-testid="social">
      <div className="flex flex-wrap items-center gap-2">
        <ReactionBar reactions={reactions} onReact={onReact} />
        <Button
          size="xs"
          variant="ghost"
          icon={<MessageSquare />}
          aria-expanded={open}
          onClick={(e) => {
            e.stopPropagation();
            setOpen((o) => !o);
          }}
        >
          {commentsLabel(comments) || "Comment"}
        </Button>
      </div>
      {open && <CommentThread a={a} bump={bump} />}
    </div>
  );
}
