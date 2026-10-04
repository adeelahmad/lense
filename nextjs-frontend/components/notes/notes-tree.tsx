"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, FileText, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type DragEvent } from "react";

import { Notes } from "@/app/openapi-client";
import type { NotePageItem as PageItem } from "@/app/openapi-client/types.gen";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const OPEN_KEY = "lens.notes.open";

function readOpen(): Set<number> {
  try {
    return new Set(JSON.parse(localStorage.getItem(OPEN_KEY) ?? "[]") as number[]);
  } catch {
    return new Set();
  }
}

/** The free notes of the namespaces in view, as a tree in the left navigation (like Notion): open a note, add one
 * inside another, drag one onto another to move it there, or onto a namespace's name to move it to the top. Pages of
 * resources and entities aren't here: they open from those things. */
export function NotesTree({ onNavigate }: { onNavigate?: () => void }) {
  const { namespace, namespaces } = useArchive();
  const names = namespace ? [namespace] : namespaces.map((n) => n.name);
  const [open, setOpen] = useState<Set<number>>(() => new Set());
  useEffect(() => setOpen(readOpen()), []);
  const toggle = (id: number) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem(OPEN_KEY, JSON.stringify([...next]));
      } catch {
        /* the tree just forgets what was open */
      }
      return next;
    });
  if (!names.length) return null;
  return (
    <div className="mt-2 flex min-h-0 flex-col gap-0.5 overflow-y-auto border-t border-border pt-2" aria-label="Notes">
      {names.map((ns) => (
        <NamespaceNotes key={ns} ns={ns} showName={!namespace} open={open} toggle={toggle} onNavigate={onNavigate} />
      ))}
    </div>
  );
}

function NamespaceNotes({
  ns,
  showName,
  open,
  toggle,
  onNavigate,
}: {
  ns: string;
  showName: boolean;
  open: Set<number>;
  toggle: (id: number) => void;
  onNavigate?: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { can } = useArchive();
  const canEdit = can("editor", ns);
  const tree = useQuery({
    queryKey: ["notes-tree", ns],
    queryFn: () => data(Notes.listPages({ client, query: { ns } })),
  });
  const kids = useMemo(() => {
    const m = new Map<number | null, PageItem[]>();
    for (const p of tree.data?.pages ?? []) {
      const k = p.parent ?? null;
      m.set(k, [...(m.get(k) ?? []), p]);
    }
    return m;
  }, [tree.data]);
  const fail = (title: string) => (err: unknown) =>
    toast({ title, body: err instanceof ApiError ? err.message : "Please try again.", tone: "red" });
  const create = useMutation({
    mutationFn: (parent: number | null) => data(Notes.createPage({ client, body: { ns, title: "Untitled", parent } })),
    onSuccess: (p, parent) => {
      if (parent != null && !open.has(parent)) toggle(parent);
      qc.invalidateQueries({ queryKey: ["notes-tree", ns] });
      router.push(`/notes/${p.id}`);
      onNavigate?.();
    },
    onError: fail("Couldn’t add a note"),
  });
  const move = useMutation({
    mutationFn: ({ id, parent }: { id: number; parent: number | null }) =>
      data(Notes.movePage({ client, path: { pid: id }, body: { parent } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes-tree", ns] }),
    onError: fail("Couldn’t move the note"),
  });
  const [over, setOver] = useState<number | "top" | null>(null);
  const drop = (parent: number | null) => (e: DragEvent) => {
    e.preventDefault();
    setOver(null);
    const id = Number(e.dataTransfer.getData("application/x-lens-note"));
    const from = e.dataTransfer.getData("application/x-lens-ns");
    if (!id || from !== ns || id === parent) return;
    move.mutate({ id, parent });
    if (parent != null && !open.has(parent)) toggle(parent);
  };
  const allow = (target: number | "top") => (e: DragEvent) => {
    if (!canEdit || !e.dataTransfer.types.includes("application/x-lens-note")) return;
    e.preventDefault();
    setOver(target);
  };

  const top = kids.get(null) ?? [];
  return (
    <div className="flex flex-col gap-0.5">
      <div
        className={cn(
          "group flex h-7 items-center gap-1 rounded-sm px-2.5 text-[11px] font-bold uppercase tracking-[.06em] text-fg-muted",
          over === "top" && "bg-blue-surface",
        )}
        onDragOver={allow("top")}
        onDragLeave={() => setOver(null)}
        onDrop={drop(null)}
      >
        <Link href="/notes" onClick={onNavigate} className="flex-1 truncate hover:text-fg-strong">
          {showName ? `Notes · ${ns}` : "Notes"}
        </Link>
        {canEdit && (
          <button
            type="button"
            onClick={() => create.mutate(null)}
            aria-label={`New note in ${ns}`}
            className="grid size-5 place-items-center rounded-sm text-fg-muted hover:bg-surface-neutral hover:text-fg-strong"
          >
            <Plus className="size-3.5" />
          </button>
        )}
      </div>
      {tree.isSuccess && !top.length && <p className="px-2.5 pb-1 text-[12px] text-fg-muted">No notes yet.</p>}
      {top.map((p) => (
        <Node
          key={p.id}
          page={p}
          depth={0}
          ns={ns}
          kids={kids}
          open={open}
          toggle={toggle}
          canEdit={canEdit}
          over={over}
          allow={allow}
          drop={drop}
          onLeave={() => setOver(null)}
          onAdd={(parent) => create.mutate(parent)}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  );
}

function Node({
  page,
  depth,
  ns,
  kids,
  open,
  toggle,
  canEdit,
  over,
  allow,
  drop,
  onLeave,
  onAdd,
  onNavigate,
}: {
  page: PageItem;
  depth: number;
  ns: string;
  kids: Map<number | null, PageItem[]>;
  open: Set<number>;
  toggle: (id: number) => void;
  canEdit: boolean;
  over: number | "top" | null;
  allow: (target: number) => (e: DragEvent) => void;
  drop: (parent: number) => (e: DragEvent) => void;
  onLeave: () => void;
  onAdd: (parent: number) => void;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const children = kids.get(page.id) ?? [];
  const expanded = open.has(page.id);
  const on = pathname === `/notes/${page.id}`;
  return (
    <div>
      <div
        className={cn(
          "group flex h-[30px] items-center gap-1 rounded-sm pr-1 text-[13.5px]",
          on ? "bg-blue-surface font-bold text-fg-accent" : "text-fg-strong hover:bg-surface-neutral",
          over === page.id && "ring-2 ring-inset ring-blue",
        )}
        style={{ paddingLeft: 4 + depth * 14 }}
        draggable={canEdit}
        onDragStart={(e) => {
          e.dataTransfer.setData("application/x-lens-note", String(page.id));
          e.dataTransfer.setData("application/x-lens-ns", ns);
          e.dataTransfer.effectAllowed = "move";
        }}
        onDragOver={allow(page.id)}
        onDragLeave={onLeave}
        onDrop={drop(page.id)}
      >
        <button
          type="button"
          onClick={() => toggle(page.id)}
          aria-label={expanded ? `Close ${page.title}` : `Open ${page.title}`}
          aria-expanded={expanded}
          className={cn(
            "grid size-5 shrink-0 place-items-center rounded-sm text-fg-muted hover:bg-surface-neutral",
            !children.length && "invisible",
          )}
        >
          <ChevronRight className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
        </button>
        <Link
          href={`/notes/${page.id}`}
          onClick={onNavigate}
          aria-current={on ? "page" : undefined}
          title={page.summary ?? page.title}
          className="flex min-w-0 flex-1 items-center gap-1.5"
        >
          {page.author === "assistant" ? (
            <Sparkles className="size-3.5 shrink-0 text-fg-muted" aria-label="Written by the assistant" />
          ) : (
            <FileText className="size-3.5 shrink-0 text-fg-muted" aria-hidden />
          )}
          <span className="truncate">{page.title}</span>
        </Link>
        {canEdit && (
          <button
            type="button"
            onClick={() => onAdd(page.id)}
            aria-label={`New note inside ${page.title}`}
            className="grid size-5 shrink-0 place-items-center rounded-sm text-fg-muted opacity-0 hover:bg-surface-neutral hover:text-fg-strong focus:opacity-100 group-hover:opacity-100"
          >
            <Plus className="size-3.5" />
          </button>
        )}
      </div>
      {expanded &&
        children.map((c) => (
          <Node
            key={c.id}
            page={c}
            depth={depth + 1}
            ns={ns}
            kids={kids}
            open={open}
            toggle={toggle}
            canEdit={canEdit}
            over={over}
            allow={allow}
            drop={drop}
            onLeave={onLeave}
            onAdd={onAdd}
            onNavigate={onNavigate}
          />
        ))}
    </div>
  );
}
