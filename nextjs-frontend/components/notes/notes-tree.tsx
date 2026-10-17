"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpToLine,
  ChevronRight,
  Download,
  Ellipsis,
  FileText,
  FolderInput,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type DragEvent } from "react";

import { Notes } from "@/app/openapi-client";
import type { NotePageItem as PageItem } from "@/app/openapi-client/types.gen";
import { useDeleteNote } from "@/components/notes/delete";
import { useNoteDownloads } from "@/components/notes/download";
import { usePin } from "@/components/notes/pin";
import { Dialog } from "@/components/ui/dialog";
import { FindBox } from "@/components/ui/find-box";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
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
 * inside another, drag one onto another to move it there, or onto a namespace's name to move it to the top. Each row's
 * menu (⋯, always showing on a touch screen) adds a note inside, renames, moves (no dragging needed) and deletes, with
 * Undo, and pins it: pinned notes (each person's own) also show at the top. Pages of resources and entities aren't here:
 * they open from those things. */
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
    <div className="mt-2 flex flex-col gap-0.5 border-t border-border pt-2" aria-label="Notes">
      <Pinned names={names} showName={!namespace} onNavigate={onNavigate} />
      {names.map((ns) => (
        <NamespaceNotes key={ns} ns={ns} showName={!namespace} open={open} toggle={toggle} onNavigate={onNavigate} />
      ))}
    </div>
  );
}

/** The notes this person pinned, in every namespace in view, above the tree: one click to open, one to unpin. */
function Pinned({ names, showName, onNavigate }: { names: string[]; showName: boolean; onNavigate?: () => void }) {
  const client = useApiClient();
  const pathname = usePathname();
  const pin = usePin();
  const trees = useQueries({
    queries: names.map((ns) => ({
      queryKey: ["notes-tree", ns],
      queryFn: () => data(Notes.listPages({ client, query: { ns } })),
    })),
  });
  const pinned = trees.flatMap((t) =>
    t.data ? t.data.pages.filter((p) => p.pinned).map((p) => ({ ...p, ns: t.data.namespace })) : [],
  );
  if (!pinned.length) return null;
  return (
    <div className="flex flex-col gap-0.5 pb-1" role="group" aria-label="Pinned notes">
      <div className="flex h-7 items-center px-2.5 text-[11px] font-bold uppercase tracking-[.06em] text-fg-muted">
        Pinned
      </div>
      {pinned.map((p) => {
        const on = pathname === `/notes/${p.id}`;
        return (
          <div
            key={`${p.ns}:${p.id}`}
            className={cn(
              "group flex h-[30px] items-center gap-1 rounded-sm pl-2.5 pr-1 text-[13.5px]",
              on ? "bg-blue-surface font-bold text-fg-accent" : "text-fg-strong hover:bg-surface-neutral",
            )}
          >
            <Link
              href={`/notes/${p.id}`}
              onClick={onNavigate}
              aria-current={on ? "page" : undefined}
              title={showName ? `${p.title} · ${p.ns}` : p.title}
              className="flex min-w-0 flex-1 items-center gap-1.5"
            >
              <Pin className="size-3.5 shrink-0 text-fg-muted" aria-hidden />
              <span className="truncate">{p.title}</span>
            </Link>
            <button
              type="button"
              onClick={() => void pin(p.ns, p.id, false)}
              aria-label={`Unpin ${p.title}`}
              className={rowButton}
            >
              <PinOff className="size-3.5" />
            </button>
          </div>
        );
      })}
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
      router.push(`/notes/${p.id}?new=1`);
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
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) =>
      data(Notes.updatePage({ client, path: { pid: id }, body: { title } })),
    onSuccess: (p) => {
      qc.setQueryData(["note", p.id], p);
      qc.invalidateQueries({ queryKey: ["notes-tree", ns] });
    },
    onError: fail("Couldn’t rename the note"),
  });
  const deleteNote = useDeleteNote();
  const pin = usePin();
  const downloads = useNoteDownloads();
  const pathname = usePathname();
  const [renaming, setRenaming] = useState<number | null>(null);
  const [moving, setMoving] = useState<PageItem | null>(null);
  const actions: RowActions = {
    add: (parent) => create.mutate(parent),
    rename: (id, title) => {
      setRenaming(null);
      const was = tree.data?.pages.find((p) => p.id === id)?.title;
      if (title.trim() && title.trim() !== was) rename.mutate({ id, title: title.trim() });
    },
    startRename: setRenaming,
    moveTo: (id, parent) => {
      move.mutate({ id, parent });
      if (parent != null && !open.has(parent)) toggle(parent);
    },
    startMove: setMoving,
    pin: (p, on) => void pin(ns, p.id, on),
    download: (p) => void downloads.note(p.id, p.title),
    remove: async (p) => {
      // deleting the note that's open leaves it for the Notes page
      if ((await deleteNote(p.id, p.title)) && pathname === `/notes/${p.id}`) router.push("/notes");
    },
  };
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
          actions={actions}
          renaming={renaming}
          onNavigate={onNavigate}
        />
      ))}
      {moving && (
        <MoveTo
          page={moving}
          pages={tree.data?.pages ?? []}
          kids={kids}
          ns={ns}
          onPick={(parent) => {
            setMoving(null);
            actions.moveTo(moving.id, parent);
          }}
          onClose={() => setMoving(null)}
        />
      )}
    </div>
  );
}

type RowActions = {
  add: (parent: number) => void;
  startRename: (id: number) => void;
  rename: (id: number, title: string) => void;
  moveTo: (id: number, parent: number | null) => void;
  startMove: (page: PageItem) => void;
  pin: (page: PageItem, on: boolean) => void;
  download: (page: PageItem) => void;
  remove: (page: PageItem) => void;
};

/** The notes a note is inside, and those inside it, can't be where it goes. */
function inside(kids: Map<number | null, PageItem[]>, id: number): Set<number> {
  const out = new Set<number>([id]);
  const walk = (at: number) => {
    for (const c of kids.get(at) ?? []) {
      if (out.has(c.id)) continue;
      out.add(c.id);
      walk(c.id);
    }
  };
  walk(id);
  return out;
}

/** "Move to…": find the note to put it inside, or the top of the tree. Enter moves it to the first one found. */
function MoveTo({
  page,
  pages,
  kids,
  ns,
  onPick,
  onClose,
}: {
  page: PageItem;
  pages: PageItem[];
  kids: Map<number | null, PageItem[]>;
  ns: string;
  onPick: (parent: number | null) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const not = inside(kids, page.id);
  const words = q.trim().toLowerCase();
  const TOP = 0; // FindBox items need a number: the top of the tree
  const items = [
    ...(page.parent != null && (!words || "top".includes(words))
      ? [{ id: TOP, name: `The top of ${ns}’s notes` }]
      : []),
    ...pages
      .filter((p) => !not.has(p.id) && p.id !== page.parent && (!words || p.title.toLowerCase().includes(words)))
      .slice(0, 8)
      .map((p) => ({ id: p.id, name: p.title })),
  ];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Move “${page.title}”`}>
      <FindBox
        q={q}
        onQ={setQ}
        items={items}
        empty={words ? `No note is called “${q.trim()}”.` : "No other notes to put it in."}
        onPick={(it) => onPick(it.id === TOP ? null : it.id)}
        onCancel={onClose}
        placeholder="The note to put it in"
        label="Move into"
        autoFocus
      />
    </Dialog>
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
  actions,
  renaming,
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
  actions: RowActions;
  renaming: number | null;
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
        {renaming === page.id ? (
          <input
            aria-label={`New name for ${page.title}`}
            defaultValue={page.title}
            autoFocus
            onFocus={(e) => e.currentTarget.select()}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing) return;
              if (e.key === "Enter") actions.rename(page.id, e.currentTarget.value);
              else if (e.key === "Escape") actions.rename(page.id, page.title);
            }}
            onBlur={(e) => actions.rename(page.id, e.currentTarget.value)}
            maxLength={200}
            className="h-6 min-w-0 flex-1 rounded-xs border border-blue bg-background px-1.5 text-[13.5px] font-normal text-fg outline-hidden"
          />
        ) : (
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
        )}
        {renaming !== page.id && (
          <>
            <Menu>
              <MenuTrigger asChild>
                <button type="button" aria-label={`More for ${page.title}`} className={rowButton}>
                  <Ellipsis className="size-3.5" />
                </button>
              </MenuTrigger>
              <MenuContent align="start">
                {canEdit && (
                  <>
                    <MenuItem icon={<Plus />} onSelect={() => actions.add(page.id)}>
                      Add a note inside
                    </MenuItem>
                    <MenuItem icon={<Pencil />} onSelect={() => actions.startRename(page.id)}>
                      Rename
                    </MenuItem>
                    <MenuItem icon={<FolderInput />} onSelect={() => actions.startMove(page)}>
                      Move to…
                    </MenuItem>
                    {page.parent != null && (
                      <MenuItem icon={<ArrowUpToLine />} onSelect={() => actions.moveTo(page.id, null)}>
                        Move to the top
                      </MenuItem>
                    )}
                  </>
                )}
                {/* pins are each person's own, so viewers pin too */}
                <MenuItem icon={page.pinned ? <PinOff /> : <Pin />} onSelect={() => actions.pin(page, !page.pinned)}>
                  {page.pinned ? "Unpin" : "Pin"}
                </MenuItem>
                <MenuItem icon={<Download />} onSelect={() => actions.download(page)}>
                  Download as Markdown
                </MenuItem>
                {canEdit && (
                  <>
                    <MenuSeparator />
                    <MenuItem icon={<Trash2 />} danger onSelect={() => actions.remove(page)}>
                      Delete
                    </MenuItem>
                  </>
                )}
              </MenuContent>
            </Menu>
            {canEdit && (
              <button
                type="button"
                onClick={() => actions.add(page.id)}
                aria-label={`New note inside ${page.title}`}
                className={rowButton}
              >
                <Plus className="size-3.5" />
              </button>
            )}
          </>
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
            actions={actions}
            renaming={renaming}
            onNavigate={onNavigate}
          />
        ))}
    </div>
  );
}

// A row's buttons show on hover or focus with a mouse, and always on a touch screen (no hover there)
const rowButton =
  "grid size-5 shrink-0 place-items-center rounded-sm text-fg-muted opacity-0 hover:bg-surface-neutral hover:text-fg-strong focus:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100 [@media(hover:none)]:opacity-100";
