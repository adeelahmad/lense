"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ChevronRight,
  Download,
  Ellipsis,
  FileText,
  History,
  Link2,
  Pin,
  PinOff,
  Plus,
  Shapes,
  Sparkles,
  Trash2,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Notes } from "@/app/openapi-client";
import type { NoteCrumb, NotePage as Page, NotePageDraft as PageDraft } from "@/app/openapi-client/types.gen";
import { ActivityPanel } from "@/components/costs/costs";
import type { EditorBlobs, EditorChange, LinkTarget } from "@/components/notes/block-editor";
import { useDeleteNote } from "@/components/notes/delete";
import { useNoteDownloads } from "@/components/notes/download";
import { HomeSuggestion } from "@/components/notes/home-suggestions";
import { LinkSuggestions } from "@/components/notes/link-suggestions";
import { NoteHistory } from "@/components/notes/note-history";
import { PLACES, hrefFor } from "@/components/notes/links";
import { usePin } from "@/components/notes/pin";
import { Button, IconButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const BlockEditor = dynamic(() => import("@/components/notes/block-editor"), {
  ssr: false,
  loading: () => <Skeleton className="h-[240px] w-full" />,
});

type View = "page" | "edgeless";
type Changes = {
  title?: string;
  summary?: string;
  date?: string;
  place?: string;
  body?: string;
  doc?: string;
  view?: View;
};

/** A note: a free note (`id`), or the page of a thing (`about`, like "entity:5"), which is a draft until someone writes
 * on it. Everything saves as you type. `embedded`: the notes part of a page that shows the thing itself (an entity's
 * wiki page), which has its own title and description, so only the text, its view, history and links show. */
export function NotePage({ id, about, embedded = false }: { id?: number; about?: string; embedded?: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const key = id ? ["note", id] : ["note-about", about];
  const q = useQuery({
    queryKey: key,
    queryFn: () => {
      if (id) return data(Notes.getPage({ client, path: { pid: id } }));
      const [kind, k] = (about ?? "").split(":");
      return data(Notes.pageAbout({ client, path: { kind, key: Number(k) } }));
    },
  });
  const page = q.data as (Page | PageDraft) | undefined;
  const saved = page && page.id != null ? (page as Page) : null;

  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [date, setDate] = useState("");
  const [place, setPlace] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [restored, setRestored] = useState(0); // a restore (or an added link) brings the editor back with the new text
  const [linking, setLinking] = useState(false);
  const [view, setView] = useState<View>("page");
  const [words, setWords] = useState(0);
  const loadedFor = useRef<string | null>(null);
  // Just made with New note (?new=1): the title takes the focus with "Untitled" selected, so typing names it
  const fresh = useRef(useSearchParams().get("new") === "1");
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!page) return;
    const k = page.about ?? `page:${page.id}`;
    if (loadedFor.current === k) return;
    loadedFor.current = k;
    setTitle(page.title);
    setSummary(saved?.summary ?? "");
    setDate(saved?.date ?? "");
    setPlace(saved?.place ?? "");
    setView((saved?.view as View | null) ?? "page");
    setWords(wordCount(page.body ?? ""));
  }, [page, saved]);
  useEffect(() => {
    const el = titleRef.current;
    if (!fresh.current || !el || !title || !page?.can_edit) return;
    fresh.current = false;
    el.focus();
    el.select();
    window.history.replaceState(window.history.state, "", window.location.pathname);
  }, [title, page?.can_edit]);

  // Saving: changes gather for a moment, then go in one request. A thing's draft becomes its page on the first one.
  const pending = useRef<Changes>({});
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const savedId = useRef<number | null>(null);
  savedId.current = saved?.id ?? savedId.current;
  const [state, setState] = useState<"saved" | "saving" | "error">("saved");
  const now = useRef({ page, title, key });
  now.current = { page, title, key };

  // The editor's images and attachments: kept on the server once the page exists, until then held back
  const queued = useRef<[string, Blob][]>([]);
  const putBlob = useCallback(
    async (pid: number, key: string, blob: Blob) => {
      try {
        await data(
          Notes.putBlob({
            client,
            path: { pid },
            query: { key, name: blob instanceof File ? blob.name : null },
            body: blob,
            headers: { "Content-Type": blob.type || "application/octet-stream" },
          }),
        );
      } catch (err) {
        toast({
          title: "Couldn’t keep the file",
          body: err instanceof ApiError ? err.message : "Please try again.",
          tone: "red",
        });
      }
    },
    [client, toast],
  );
  const blobs = useMemo<EditorBlobs>(
    () => ({
      get: async (key) => {
        if (savedId.current == null) return null;
        return (await data(
          Notes.getBlob({ client, path: { pid: savedId.current }, query: { key }, parseAs: "blob" }),
        )) as unknown as Blob;
      },
      set: async (key, blob) => {
        if (savedId.current == null) queued.current.push([key, blob]);
        else await putBlob(savedId.current, key, blob);
      },
    }),
    [client, putBlob],
  );

  const flush = useCallback(async () => {
    clearTimeout(timer.current);
    const c = pending.current;
    pending.current = {};
    const { page, title, key } = now.current;
    if (!page || !Object.keys(c).length) return;
    setState("saving");
    try {
      let out: Page;
      if (savedId.current == null) {
        out = await data(
          Notes.createPage({
            client,
            body: {
              ns: page.namespace,
              about: page.about ?? undefined,
              title: (c.title ?? title).trim() || page.title,
              body: c.body ?? "",
              doc: c.doc ?? null,
              summary: c.summary || null,
              date: c.date || null,
              place: (c.place || null) as Page["place"],
              view: c.view ?? null,
            },
          }),
        );
        savedId.current = out.id;
        for (const [k, b] of queued.current.splice(0)) void putBlob(out.id, k, b);
      } else {
        const body: Record<string, unknown> = {};
        if (c.title !== undefined && c.title.trim()) body.title = c.title.trim();
        if (c.summary !== undefined) body.summary = c.summary;
        if (c.date !== undefined && c.date) body.date = c.date;
        if (c.place !== undefined) body.place = c.place;
        if (c.view !== undefined) body.view = c.view;
        if (c.body !== undefined) {
          body.body = c.body;
          body.doc = c.doc ?? null;
        }
        out = await data(Notes.updatePage({ client, path: { pid: savedId.current }, body }));
      }
      qc.setQueryData(key, out);
      qc.invalidateQueries({ queryKey: ["notes-tree"] });
      setState("saved");
    } catch (err) {
      setState("error");
      toast({
        title: "Couldn’t save the note",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      });
    }
  }, [client, qc, toast]);

  const change = useCallback(
    (c: Changes) => {
      pending.current = { ...pending.current, ...c };
      clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), 800);
    },
    [flush],
  );
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => () => void flushRef.current(), []);

  const search = useCallback(
    async (sign: "@" | "#", query: string): Promise<LinkTarget[]> =>
      page ? data(Notes.linkTargets({ client, query: { ns: page.namespace, sign, q: query, limit: 20 } })) : [],
    [client, page],
  );
  const openLink = useCallback(
    (target: string) => {
      const href = hrefFor(target, page?.namespace);
      if (href) router.push(href);
    },
    [router, page?.namespace],
  );

  // A new note inside this one, opened with its title ready to type
  const [adding, setAdding] = useState(false);
  const addInside = async () => {
    if (savedId.current == null || !page) return;
    setAdding(true);
    try {
      const p = await data(
        Notes.createPage({ client, body: { ns: page.namespace, title: "Untitled", parent: savedId.current } }),
      );
      qc.invalidateQueries({ queryKey: ["notes-tree"] });
      router.push(`/notes/${p.id}?new=1`);
    } catch (err) {
      toast({
        title: "Couldn’t add a note",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      });
    } finally {
      setAdding(false);
    }
  };
  const pin = usePin();
  const downloads = useNoteDownloads();

  // No question first: the notice has Undo, and the note waits in recently deleted
  const deleteNote = useDeleteNote();
  const remove = async () => {
    if (savedId.current == null) return;
    await flush(); // what was just typed is kept with it, for Undo
    if (!(await deleteNote(savedId.current, title.trim() || page?.title || "Untitled"))) return;
    if (!embedded) return router.push("/notes");
    // the thing's own page stays, with an empty draft of its notes
    await qc.invalidateQueries({ queryKey: key });
    savedId.current = null;
    loadedFor.current = null;
    setRestored((n) => n + 1);
  };

  if (q.isError)
    return (
      <EmptyState icon={<FileText />} title="This note isn’t here" tone="error">
        It was deleted, or you don’t have access to it.
      </EmptyState>
    );
  if (!page)
    return (
      <div className={cn("flex flex-col gap-4", !embedded && "mx-auto max-w-[860px] px-6 py-8")}>
        {!embedded && <Skeleton className="h-10 w-2/3" />}
        {!embedded && <Skeleton className="h-5 w-1/2" />}
        <Skeleton className="h-[240px] w-full" />
      </div>
    );

  const canEdit = page.can_edit;
  const aboutHref = page.about && !embedded ? hrefFor(page.about, page.namespace) : null;
  const views = (
    <div role="radiogroup" aria-label="Show as" className="inline-flex rounded-pill border border-border p-0.5">
      {(
        [
          ["page", "Page", FileText],
          ["edgeless", "Canvas", Shapes],
        ] as const
      ).map(([v, label, Icon]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={view === v}
          onClick={() => {
            setView(v);
            if (canEdit) change({ view: v });
          }}
          className={cn(
            "inline-flex items-center gap-1 rounded-pill px-2.5 py-0.5",
            view === v ? "bg-blue-surface font-bold text-fg-accent" : "text-fg-secondary hover:text-fg",
          )}
        >
          <Icon className="size-3.5" /> {label}
        </button>
      ))}
    </div>
  );
  return (
    <article className={cn("flex w-full flex-col gap-3", !embedded && "mx-auto max-w-[860px] px-6 py-8")}>
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-muted">
        {!embedded && <Breadcrumbs namespace={page.namespace} path={saved?.path ?? []} />}
        {aboutHref && (
          <Link href={aboutHref} className="inline-flex items-center gap-1 text-fg-accent hover:underline">
            Open {page.about?.split(":")[0]} <ArrowUpRight className="size-3.5" />
          </Link>
        )}
        {saved?.author === "assistant" && (
          <span className="inline-flex items-center gap-1">
            <Sparkles className="size-3.5" /> Written by the assistant
          </span>
        )}
        <span className="flex-1" />
        {embedded && views}
        {view === "page" && words > 0 && <span>{words === 1 ? "1 word" : `${words.toLocaleString()} words`}</span>}
        {canEdit && (
          <span aria-live="polite">
            {state === "saving" ? "Saving…" : state === "error" ? "Not saved" : saved ? "Saved" : "Draft"}
          </span>
        )}
        {saved && (
          <Menu>
            <MenuTrigger asChild>
              <IconButton label="More on this note" size={28}>
                <Ellipsis />
              </IconButton>
            </MenuTrigger>
            <MenuContent align="end">
              {canEdit && !saved.about && (
                <MenuItem icon={<Plus />} disabled={adding} onSelect={() => void addInside()}>
                  Add a note inside
                </MenuItem>
              )}
              <MenuItem
                icon={saved.pinned ? <PinOff /> : <Pin />}
                onSelect={() => void pin(page.namespace, saved.id, !saved.pinned)}
              >
                {saved.pinned ? "Unpin" : "Pin"}
              </MenuItem>
              <MenuItem icon={<History />} onSelect={() => setShowHistory(true)}>
                History
              </MenuItem>
              <MenuItem icon={<Download />} onSelect={() => void downloads.note(saved.id, title.trim() || saved.title)}>
                Download as Markdown
              </MenuItem>
              {canEdit && (
                <>
                  <MenuSeparator />
                  <MenuItem icon={<Trash2 />} danger onSelect={() => void remove()}>
                    Delete
                  </MenuItem>
                </>
              )}
            </MenuContent>
          </Menu>
        )}
      </div>
      {!embedded && (
        <>
          <input
            aria-label="Title"
            ref={titleRef}
            value={title}
            readOnly={!canEdit}
            onChange={(e) => {
              setTitle(e.target.value);
              change({ title: e.target.value });
            }}
            onKeyDown={(e) => {
              // Enter goes on to the page's first line, as in a document
              if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
              // (the page root is what takes the focus; the caret goes in its first paragraph, not the hidden title line)
              const root = document.querySelector<HTMLElement>(".lens-block-editor affine-page-root");
              const line = root?.querySelector(".inline-editor");
              if (!root || !line) return;
              e.preventDefault();
              root.focus();
              window.getSelection()?.collapse(line, 0);
            }}
            placeholder="Untitled"
            className="w-full bg-transparent text-[30px] font-bold leading-tight tracking-[-.015em] text-fg outline-hidden placeholder:text-fg-muted"
          />
          <input
            aria-label="Summary"
            value={summary}
            readOnly={!canEdit}
            maxLength={300}
            onChange={(e) => {
              setSummary(e.target.value);
              change({ summary: e.target.value });
            }}
            placeholder={canEdit ? "One line on what this note holds (the assistant reads it first)" : ""}
            className="w-full bg-transparent text-[15px] text-fg-secondary outline-hidden placeholder:text-fg-muted"
          />
          <div className="flex flex-wrap items-center gap-3 border-b border-border pb-3 text-[13px] text-fg-secondary">
            <label className="flex items-center gap-1.5">
              Date
              <input
                type="date"
                value={date}
                readOnly={!canEdit}
                onChange={(e) => {
                  setDate(e.target.value);
                  change({ date: e.target.value });
                }}
                className="rounded-sm border border-border bg-background px-2 py-0.5 text-fg"
              />
            </label>
            <label className="flex items-center gap-1.5">
              Filed under
              <select
                value={place}
                disabled={!canEdit}
                onChange={(e) => {
                  setPlace(e.target.value);
                  change({ place: e.target.value });
                }}
                className="rounded-sm border border-border bg-background px-2 py-0.5 text-fg"
              >
                <option value="">Not filed</option>
                {PLACES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            {canEdit && !place && saved?.place_suggestion && (
              <button
                type="button"
                onClick={() => {
                  setPlace(saved.place_suggestion!.place);
                  change({ place: saved.place_suggestion!.place });
                }}
                className="inline-flex items-center gap-1 rounded-pill border border-border px-2 py-0.5 text-fg-accent hover:bg-blue-surface"
                title="The assistant wasn’t sure; file it there?"
              >
                <Sparkles className="size-3.5" />
                File under {PLACES.find((x) => x.value === saved.place_suggestion!.place)?.label}
              </button>
            )}
            {canEdit && saved && !saved.about && saved.parent == null && (
              <HomeSuggestion
                pid={saved.id}
                version={saved.updated_at}
                busy={linking}
                onMove={async (s) => {
                  setLinking(true);
                  try {
                    await flush();
                    const out = await data(
                      Notes.movePage({ client, path: { pid: saved.id }, body: { parent: s.page } }),
                    );
                    qc.setQueryData(key, out);
                    qc.invalidateQueries({ queryKey: ["notes-tree"] });
                  } catch (err) {
                    toast({
                      title: "Couldn’t move it",
                      body: err instanceof ApiError ? err.message : "Please try again.",
                      tone: "red",
                    });
                  } finally {
                    setLinking(false);
                  }
                }}
              />
            )}
            {saved?.updated_at && <span>Changed {shortDate(saved.updated_at)}</span>}
            <span className="flex-1" />
            {views}
          </div>
        </>
      )}
      {embedded && canEdit && !(page.body ?? "").trim() && (
        <p className="m-0 text-[13px] text-fg-muted">
          Nothing written on {page.title} yet. Write below: @ links people, things and recordings, # links topics.
        </p>
      )}
      <BlockEditor
        key={`${page.about ?? `page:${page.id}`}:${restored}`}
        className={embedded && view === "page" ? "rounded-md border border-border px-4 py-2" : undefined}
        markdown={page.body ?? ""}
        doc={saved?.doc ?? null}
        readOnly={!canEdit}
        view={view}
        stale={Boolean(saved?.doc_stale)}
        onChange={(c: EditorChange) => {
          setWords(wordCount(c.markdown));
          change({ body: c.markdown, doc: c.doc });
        }}
        search={search}
        onOpenLink={openLink}
        blobs={blobs}
      />
      {canEdit && saved && (
        <LinkSuggestions
          pid={saved.id}
          version={saved.updated_at}
          busy={linking}
          onLink={async (s) => {
            // whatever is being typed goes first; the link is added on a line of its own, and the editor reloads
            setLinking(true);
            try {
              await flush();
              const cur = qc.getQueryData<Page>(key) ?? saved;
              const body = `${(cur.body ?? "").trimEnd()}\n\n${s.sign}[${s.label}](${s.target})`;
              const out = await data(Notes.updatePage({ client, path: { pid: saved.id }, body: { body } }));
              qc.setQueryData(key, out);
              setRestored((n) => n + 1);
            } catch (err) {
              toast({
                title: "Couldn’t link it",
                body: err instanceof ApiError ? err.message : "Please try again.",
                tone: "red",
              });
            } finally {
              setLinking(false);
            }
          }}
        />
      )}
      {saved && !saved.about && !embedded && (
        <Inside
          items={saved.children ?? []}
          canEdit={Boolean(canEdit)}
          adding={adding}
          onAdd={() => void addInside()}
        />
      )}
      <Backlinks items={page.backlinks ?? []} />
      {showHistory && saved && (
        <NoteHistory
          pid={saved.id}
          canEdit={Boolean(canEdit)}
          onClose={() => setShowHistory(false)}
          onRestored={(p) => {
            clearTimeout(timer.current);
            pending.current = {};
            qc.setQueryData(key, p);
            qc.invalidateQueries({ queryKey: ["note-history", p.id] });
            qc.invalidateQueries({ queryKey: ["notes-tree"] });
            loadedFor.current = null;
            setRestored((n) => n + 1);
            setShowHistory(false);
          }}
        />
      )}
      {saved && !embedded && <CostsFold resource={`note_page:${saved.id}`} />}
    </article>
  );
}

/** Words as a reader counts them: the text of links, not their addresses, list numbers or Markdown's marks. */
export function wordCount(markdown: string): number {
  const text = markdown
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?/gm, "")
    .replace(/[@#]?\[([^\]\n]*)\]\([^)\n]*\)/g, "$1")
    .replace(/<[^>\n]+>/g, " ");
  return text.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
}

/** Notes › namespace › the notes this one is inside, each a link. */
function Breadcrumbs({ namespace, path }: { namespace: string; path: NoteCrumb[] }) {
  const sep = <ChevronRight aria-hidden className="size-3.5 shrink-0" />;
  return (
    <nav aria-label="Where this note is" className="flex min-w-0 flex-wrap items-center gap-1">
      <Link href="/notes" className="hover:text-fg hover:underline">
        Notes
      </Link>
      {sep}
      <span>{namespace}</span>
      {path.map((c) => (
        <span key={c.id} className="inline-flex min-w-0 items-center gap-1">
          {sep}
          <Link href={`/notes/${c.id}`} className="max-w-[24ch] truncate hover:text-fg hover:underline">
            {c.title}
          </Link>
        </span>
      ))}
    </nav>
  );
}

/** The notes inside this one, in the tree's order, with Add. */
function Inside({
  items,
  canEdit,
  adding,
  onAdd,
}: {
  items: NoteCrumb[];
  canEdit: boolean;
  adding: boolean;
  onAdd: () => void;
}) {
  if (!items.length && !canEdit) return null;
  return (
    <section className="mt-6 border-t border-border pt-4">
      <h2 className="mb-2 flex items-center gap-1.5 text-[13px] font-bold text-fg-strong">
        <FileText className="size-4" /> Inside this note
        {items.length > 0 && <span className="font-normal text-fg-muted">{items.length}</span>}
      </h2>
      {items.length > 0 && (
        <ul className="mb-2 flex flex-col gap-1">
          {items.map((c) => (
            <li key={c.id} className="flex items-baseline gap-2">
              <Link href={`/notes/${c.id}`} className="text-[14px] text-fg-accent hover:underline">
                {c.title}
              </Link>
              {c.updated_at && <span className="text-[12px] text-fg-muted">{shortDate(c.updated_at)}</span>}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <Button variant="ghost" size="xs" icon={<Plus />} onClick={onAdd} disabled={adding} className="-ml-3">
          Add a note inside
        </Button>
      )}
    </section>
  );
}

/** Costs and activity, folded: most people never open it, so it loads only when they do. */
function CostsFold({ resource }: { resource: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="mt-6 border-t border-border pt-3" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer text-[13px] font-bold text-fg-strong">Costs and activity</summary>
      {open && (
        <div className="mt-3">
          <ActivityPanel resource={resource} title="Costs and activity" />
        </div>
      )}
    </details>
  );
}

function Backlinks({ items }: { items: { page: number; title: string; about?: string | null }[] }) {
  if (!items.length) return null;
  return (
    <section className="mt-6 border-t border-border pt-4">
      <h2 className="mb-2 flex items-center gap-1.5 text-[13px] font-bold text-fg-strong">
        <Link2 className="size-4" /> Linked from {items.length} {items.length === 1 ? "note" : "notes"}
      </h2>
      <ul className="flex flex-col gap-1">
        {items.map((b) => (
          <li key={b.page}>
            <Link
              href={(b.about && hrefFor(b.about)) || `/notes/${b.page}`}
              className={cn("text-[14px] text-fg-accent hover:underline")}
            >
              {b.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
