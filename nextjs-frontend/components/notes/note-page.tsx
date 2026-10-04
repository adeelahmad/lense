"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, FileText, History, Link2, Shapes, Sparkles, Trash2 } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Notes } from "@/app/openapi-client";
import type { NotePage as Page, NotePageDraft as PageDraft } from "@/app/openapi-client/types.gen";
import { ActivityPanel } from "@/components/costs/costs";
import type { EditorChange, LinkTarget } from "@/components/notes/block-editor";
import { HomeSuggestion } from "@/components/notes/home-suggestions";
import { LinkSuggestions } from "@/components/notes/link-suggestions";
import { NoteHistory } from "@/components/notes/note-history";
import { PLACES, hrefFor } from "@/components/notes/links";
import { Button } from "@/components/ui/button";
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
 * on it. Everything saves as you type. */
export function NotePage({ id, about }: { id?: number; about?: string }) {
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
  const loadedFor = useRef<string | null>(null);
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
  }, [page, saved]);

  // Saving: changes gather for a moment, then go in one request. A thing's draft becomes its page on the first one.
  const pending = useRef<Changes>({});
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const savedId = useRef<number | null>(null);
  savedId.current = saved?.id ?? savedId.current;
  const [state, setState] = useState<"saved" | "saving" | "error">("saved");
  const now = useRef({ page, title, key });
  now.current = { page, title, key };

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

  const remove = async () => {
    if (savedId.current == null) return;
    if (!window.confirm("Delete this note? The notes inside it move up a level.")) return;
    try {
      await data(Notes.deletePage({ client, path: { pid: savedId.current } }));
      qc.invalidateQueries({ queryKey: ["notes-tree"] });
      router.push("/notes");
    } catch (err) {
      toast({
        title: "Couldn’t delete it",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      });
    }
  };

  if (q.isError)
    return (
      <EmptyState icon={<FileText />} title="This note isn’t here" tone="error">
        It was deleted, or you don’t have access to it.
      </EmptyState>
    );
  if (!page)
    return (
      <div className="mx-auto flex max-w-[860px] flex-col gap-4 px-6 py-8">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-[240px] w-full" />
      </div>
    );

  const canEdit = page.can_edit;
  const aboutHref = page.about ? hrefFor(page.about, page.namespace) : null;
  return (
    <article className="mx-auto flex w-full max-w-[860px] flex-col gap-3 px-6 py-8">
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-fg-muted">
        <span>{page.namespace}</span>
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
        {canEdit && (
          <span aria-live="polite">
            {state === "saving" ? "Saving…" : state === "error" ? "Not saved" : saved ? "Saved" : "Draft"}
          </span>
        )}
        {saved && (
          <Button variant="ghost" size="xs" icon={<History />} onClick={() => setShowHistory(true)}>
            History
          </Button>
        )}
        {canEdit && saved && (
          <Button variant="danger-ghost" size="xs" icon={<Trash2 />} onClick={remove}>
            Delete
          </Button>
        )}
      </div>
      <input
        aria-label="Title"
        value={title}
        readOnly={!canEdit}
        onChange={(e) => {
          setTitle(e.target.value);
          change({ title: e.target.value });
        }}
        placeholder="Untitled"
        className="w-full bg-transparent text-[30px] font-bold leading-tight tracking-[-.015em] text-fg outline-none placeholder:text-fg-muted"
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
        className="w-full bg-transparent text-[15px] text-fg-secondary outline-none placeholder:text-fg-muted"
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
                const out = await data(Notes.movePage({ client, path: { pid: saved.id }, body: { parent: s.page } }));
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
      </div>
      <BlockEditor
        key={`${page.about ?? `page:${page.id}`}:${restored}`}
        markdown={page.body ?? ""}
        doc={saved?.doc ?? null}
        readOnly={!canEdit}
        view={view}
        stale={Boolean(saved?.doc_stale)}
        onChange={(c: EditorChange) => change({ body: c.markdown, doc: c.doc })}
        search={search}
        onOpenLink={openLink}
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
      {saved && <ActivityPanel resource={`note_page:${saved.id}`} title="Costs and activity" />}
    </article>
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
            <Link href={`/notes/${b.page}`} className={cn("text-[14px] text-fg-accent hover:underline")}>
              {b.title}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
