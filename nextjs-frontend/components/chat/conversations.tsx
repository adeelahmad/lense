"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ellipsis, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Chats } from "@/app/openapi-client";
import type { ChatSummary } from "@/app/openapi-client/types.gen";
import { fromApiScope, scopeWords } from "@/components/chat/scope";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";

function scopeLine(c: ChatSummary): string {
  const s = fromApiScope(c.scope);
  const where = s.namespaces?.length ? s.namespaces.join(", ") : scopeWords(s).replace("all your namespaces", "all namespaces");
  return `${where} · ${relative(c.updated_at ?? c.created_at)}`;
}

/** Your conversations, newest first, with New conversation on top; each can be renamed or deleted. */
export function ConversationList({
  chats,
  loading,
  error,
  activeId,
  onNew,
  className,
}: {
  chats: ChatSummary[] | undefined;
  loading: boolean;
  error: string | null;
  activeId: number | null;
  onNew: () => void;
  className?: string;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [renaming, setRenaming] = useState<ChatSummary | null>(null);
  const [title, setTitle] = useState("");
  const [deleting, setDeleting] = useState<ChatSummary | null>(null);
  const rename = useMutation({
    mutationFn: () => data(Chats.updateChat({ client, path: { cid: renaming!.id }, body: { title: title.trim() } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chats"] });
      qc.invalidateQueries({ queryKey: ["chat", renaming?.id] });
      setRenaming(null);
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => data(Chats.deleteChat({ client, path: { cid: id } })),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["chats"] });
      setDeleting(null);
      toast({ title: "Conversation deleted", tone: "intent" });
      if (id === activeId) router.push("/chat");
    },
  });
  return (
    <nav aria-label="Conversations" className={cn("flex flex-col gap-0.5", className)}>
      <div className="px-1.5 pb-2.5">
        <Button variant="secondary" size="sm" icon={<Plus />} className="w-full" onClick={onNew}>
          New conversation
        </Button>
      </div>
      {loading && (
        <div className="flex flex-col gap-3 px-2.5 py-2" aria-busy="true" aria-label="Loading conversations">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex flex-col gap-1.5">
              <Skeleton className="w-[80%]" />
              <Skeleton className="h-2.5 w-[50%]" />
            </div>
          ))}
        </div>
      )}
      {error && <Banner tone="error">{error}</Banner>}
      {chats?.length === 0 && <p className="m-0 px-2.5 py-2 text-[13px] leading-snug text-fg-secondary">No conversations yet. Ask something to start one.</p>}
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
        {chats?.map((c) => (
          <li key={c.id} className="group relative">
            <Link
              href={`/chat/${c.id}`}
              aria-current={c.id === activeId ? "page" : undefined}
              className={cn("flex flex-col gap-[3px] rounded-sm py-[9px] pl-2.5 pr-8", c.id === activeId ? "bg-blue-surface" : "hover:bg-surface-neutral")}
            >
              <span className="truncate text-[13px] font-semibold leading-snug text-fg">{c.title}</span>
              <span className="truncate text-[11.5px] text-fg-muted">{scopeLine(c)}</span>
            </Link>
            <Menu>
              <MenuTrigger asChild>
                <button
                  type="button"
                  aria-label={`More for ${c.title}`}
                  className="absolute right-1 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-full text-fg-secondary opacity-0 hover:bg-background focus:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
                >
                  <Ellipsis className="size-4" />
                </button>
              </MenuTrigger>
              <MenuContent>
                <MenuItem
                  icon={<Pencil />}
                  onSelect={() => {
                    setTitle(c.title);
                    setRenaming(c);
                  }}
                >
                  Rename
                </MenuItem>
                <MenuItem icon={<Trash2 />} danger onSelect={() => setDeleting(c)}>
                  Delete
                </MenuItem>
              </MenuContent>
            </Menu>
          </li>
        ))}
      </ul>
      <Dialog
        open={!!renaming}
        onOpenChange={(o) => !o && setRenaming(null)}
        title="Rename conversation"
        actions={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!title.trim() || rename.isPending} onClick={() => rename.mutate()}>
              Save
            </Button>
          </>
        }
      >
        <Field label="Title" error={rename.isError ? rename.error.message : undefined}>
          {({ id, invalid }) => <Input id={id} value={title} invalid={invalid} onChange={(e) => setTitle(e.target.value)} autoFocus maxLength={120} />}
        </Field>
      </Dialog>
      <Dialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete this conversation?"
        description={deleting ? `“${deleting.title}” and its answers are deleted. Recordings and batch runs it started stay.` : undefined}
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => deleting && remove.mutate(deleting.id)}>
              Delete
            </Button>
          </>
        }
      >
        {remove.isError && <Banner tone="error">{remove.error.message}</Banner>}
      </Dialog>
    </nav>
  );
}
