"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";

import { SentFiles } from "@/components/chat/attachments";
import { Button, IconButton } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** What a question that only sends files says. */
export const onlyFiles = (n: number) => (n === 1 ? "I attached this file." : "I attached these files.");

/** A question you asked, with the files sent with it. With `onEdit` (not while an answer is being written) it can be edited, which asks it again:
 * the answers and questions after it are replaced. */
export function UserBubble({
  text,
  files = [],
  onEdit,
}: {
  text: string;
  files?: { filename: string; size: number }[];
  onEdit?: (text: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const filesOnly = files.length > 0 && text === onlyFiles(files.length);
  const bubble =
    "max-w-[520px] self-end rounded-[16px_16px_4px_16px] bg-surface-neutral px-4 py-3 text-[15px] leading-normal text-fg";
  if (editing != null && onEdit) {
    const ready = editing.trim().length > 0 || files.length > 0;
    const save = () => {
      if (!ready) return;
      setEditing(null);
      if (editing.trim() !== (filesOnly ? "" : text.trim())) onEdit(editing.trim());
    };
    return (
      <div className="flex w-full flex-col gap-1.5 self-end">
        <SentFiles files={files} />
        <div className={cn(bubble, "flex w-full flex-col gap-2")}>
          <textarea
            autoFocus
            rows={Math.min(8, Math.max(2, editing.split("\n").length))}
            value={editing}
            onChange={(e) => setEditing(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                save();
              } else if (e.key === "Escape") setEditing(null);
            }}
            aria-label="Edit your question"
            className="w-full resize-none bg-transparent text-[15px] leading-normal text-fg outline-none"
          />
          <p className="m-0 text-[12.5px] text-fg-muted">Sending it replaces this answer and everything after it.</p>
          <div className="flex justify-end gap-1.5">
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={save} disabled={!ready}>
              Send
            </Button>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="group flex items-start justify-end gap-1 self-end">
      {onEdit && (
        <IconButton
          label="Edit"
          size={32}
          onClick={() => setEditing(filesOnly ? "" : text)}
          className="mt-1.5 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100 [&_svg]:size-4"
        >
          <Pencil />
        </IconButton>
      )}
      <div className="flex flex-col gap-1.5">
        <SentFiles files={files} />
        {!filesOnly && <div className={cn(bubble, "whitespace-pre-wrap")}>{text}</div>}
      </div>
    </div>
  );
}
