"use client";

import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Download, Ellipsis, FileText, NotebookPen, Pin, Plus, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Notes } from "@/app/openapi-client";
import type { NotePageItem } from "@/app/openapi-client/types.gen";
import { PLACES, pageOf } from "@/components/notes/links";
import { useNoteDownloads } from "@/components/notes/download";
import { RecentlyDeleted } from "@/components/notes/recently-deleted";
import { Button, IconButton } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { shortDate } from "@/lib/format";
import { useDebounced } from "@/lib/hooks/debounce";
import { useArchive } from "@/lib/hooks/session";

type Place = NotePageItem["place"];
type Row = {
  id: number;
  ns: string;
  title: string;
  summary?: string | null;
  snippet?: string | null;
  place?: Place;
  parent?: number | null;
  about?: string | null;
  author?: string | null;
  updated_at?: string | null;
  pinned?: boolean;
};

/** Every free note in view, the ones this person pinned first, then the latest changed, with where each sits in the
 * tree and where it's filed. Words in
 * the search box find notes by their title, summary or text (the pages of things too); the filter shows one PARA
 * place. */
export function NotesHome() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { namespace, namespaces, can } = useArchive();
  const names = namespace ? [namespace] : namespaces.map((n) => n.name);
  const [q, setQ] = useState("");
  const [place, setPlace] = useState<"" | NonNullable<Place>>("");
  const words = useDebounced(q.trim().replace(/\s+/g, " "), 200);
  const trees = useQueries({
    queries: names.map((ns) => ({
      queryKey: ["notes-tree", ns],
      queryFn: () => data(Notes.listPages({ client, query: { ns } })),
    })),
  });
  const search = useQuery({
    queryKey: ["notes-search", namespace, words, place],
    queryFn: () =>
      data(Notes.searchPages({ client, query: { q: words, ns: namespace, place: place || null, limit: 100 } })),
    enabled: !!words,
    placeholderData: keepPreviousData,
  });
  // Each free note by namespace and id, to name the notes a note is inside
  const byKey = useMemo(() => {
    const m = new Map<string, NotePageItem>();
    for (const t of trees) for (const p of t.data?.pages ?? []) m.set(`${t.data!.namespace}:${p.id}`, p);
    return m;
  }, [trees]);
  const all: Row[] = trees
    .flatMap((t) => (t.data ? t.data.pages.map((p) => ({ ...p, ns: t.data.namespace })) : []))
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) || (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  const rows: Row[] = words
    ? (search.data?.pages ?? []).map((p) => ({ ...p, ns: p.namespace }))
    : all.filter((p) => !place || p.place === place);
  const count = (v: string) => all.filter((p) => p.place === v).length;

  const target = namespace ?? names.find((n) => can("editor", n)) ?? null;
  const create = useMutation({
    mutationFn: () => data(Notes.createPage({ client, body: { ns: target!, title: "Untitled" } })),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["notes-tree"] });
      router.push(`/notes/${p.id}?new=1`);
    },
    onError: (err) =>
      toast({
        title: "Couldn’t add a note",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      }),
  });
  const editable = !!target && can("editor", target);
  const newNote = editable ? (
    <Button variant="primary" icon={<Plus />} onClick={() => create.mutate()} disabled={create.isPending}>
      New note
    </Button>
  ) : null;
  const downloads = useNoteDownloads();
  // Every namespace's notes as Markdown, for other notes apps
  const more =
    names.length > 0 ? (
      <Menu>
        <MenuTrigger asChild>
          <IconButton label="More on Notes" size={36}>
            <Ellipsis />
          </IconButton>
        </MenuTrigger>
        <MenuContent align="end">
          {names.map((ns) => (
            <MenuItem key={ns} icon={<Download />} onSelect={() => void downloads.all(ns)}>
              {names.length > 1 ? `Download ${ns}’s notes as Markdown` : "Download all as Markdown"}
            </MenuItem>
          ))}
        </MenuContent>
      </Menu>
    ) : null;
  const placeLabel = (v?: string | null) => PLACES.find((p) => p.value === v)?.label;
  /** "Home lab › Parts": the notes this one is inside, outermost first. */
  const path = (r: Row) => {
    const out: string[] = [];
    const seen = new Set<number>();
    for (let at = r.parent; at != null && !seen.has(at);) {
      seen.add(at);
      const p = byKey.get(`${r.ns}:${at}`);
      if (!p) break;
      out.unshift(p.title);
      at = p.parent;
    }
    return out.join(" › ");
  };

  const loading = trees.some((t) => t.isPending) || (!!words && search.isPending);
  return (
    <div className="mx-auto w-full max-w-[960px] px-6 py-8">
      <PageHeader
        title="Notes"
        meta={namespace ?? "All namespaces"}
        actions={
          <>
            {editable && (
              <>
                <Button asChild>
                  <Link href="/notes/today">
                    <CalendarDays />
                    Today
                  </Link>
                </Button>
                {newNote}
              </>
            )}
            {more}
          </>
        }
      />
      {(all.length > 0 || q) && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <SearchInput
            aria-label="Search notes"
            placeholder="Search notes"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              // Enter opens the first note found
              if (e.key !== "Enter" || e.nativeEvent.isComposing || !rows[0]) return;
              e.preventDefault();
              router.push(hrefOf(rows[0]));
            }}
            className="min-w-[200px] flex-1"
          />
          <div className="max-w-full overflow-x-auto">
            <Segmented
              label="Filed under"
              value={place}
              onChange={(v) => setPlace(v as typeof place)}
              items={[
                { value: "", label: "All" },
                ...PLACES.map((p) => ({
                  value: p.value,
                  label: (
                    <>
                      {SHOWN[p.value]}
                      <span className="font-normal text-fg-muted">{count(p.value) || ""}</span>
                    </>
                  ),
                })),
              ]}
            />
          </div>
        </div>
      )}
      {loading ? (
        <SkeletonRows rows={5} />
      ) : !all.length && !q ? (
        <EmptyState icon={<NotebookPen />} title="No notes yet" actions={newNote}>
          The assistant writes notes as it learns, and you can write your own. Type @ in a note to link an asset, person
          or another note, and # for a topic. Every asset and entity also has a page of its own.
        </EmptyState>
      ) : !rows.length ? (
        <EmptyState icon={<Search />} title={words ? `No notes have “${words}”` : "No notes here"}>
          {place ? `Nothing filed under ${placeLabel(place)} matches. Pick All to look everywhere.` : null}
        </EmptyState>
      ) : (
        <>
          <ul className="divide-y divide-border rounded-md border border-border">
            {rows.map((p) => {
              const inside = path(p);
              const line = (words && p.snippet) || p.summary;
              const meta = [
                placeLabel(p.place),
                !namespace ? p.ns : null,
                p.updated_at ? shortDate(p.updated_at) : null,
              ]
                .filter(Boolean)
                .join(" · ");
              return (
                <li key={`${p.ns}:${p.id}`}>
                  <Link href={hrefOf(p)} className="flex items-start gap-3 px-4 py-3 hover:bg-surface">
                    {p.author === "assistant" ? (
                      <Sparkles
                        className="mt-0.5 size-4 shrink-0 text-fg-muted"
                        aria-label="Written by the assistant"
                      />
                    ) : (
                      <FileText className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-hidden />
                    )}
                    <span className="flex min-w-0 flex-1 flex-col">
                      {(inside || p.about) && (
                        <span className="truncate text-[12px] text-fg-muted">
                          {p.about ? `Page of ${p.about.split(":")[0]}` : `In ${inside}`}
                        </span>
                      )}
                      <span className="flex min-w-0 items-center gap-1.5">
                        <span className="truncate text-[14.5px] font-bold text-fg">
                          <Marked text={p.title} words={words} />
                        </span>
                        {p.pinned && <Pin className="size-3.5 shrink-0 text-fg-muted" aria-label="Pinned" />}
                      </span>
                      {line && (
                        <span className="line-clamp-2 text-[13px] text-fg-secondary">
                          <Marked text={line} words={words} />
                        </span>
                      )}
                      {/* on a phone, under the title, which then keeps the width */}
                      <span className="text-[12.5px] text-fg-muted sm:hidden">{meta}</span>
                    </span>
                    <span className="hidden shrink-0 text-right text-[12.5px] text-fg-muted sm:block">{meta}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
          {words && search.data && search.data.total > rows.length && (
            <p className="mt-2 text-[12.5px] text-fg-muted">
              The first {rows.length} of {search.data.total}: add a word to narrow it down.
            </p>
          )}
        </>
      )}
      <RecentlyDeleted ns={namespace} />
    </div>
  );
}

/** The text with the words searched for marked, wherever they are (any case). */
function Marked({ text, words }: { text: string; words: string }) {
  if (!words) return <>{text}</>;
  const parts = text.split(new RegExp(`(${words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
  return (
    <>
      {parts.map((part, i) =>
        i % 2 ? (
          <mark key={i} className="rounded-[2px] bg-hl-word text-fg">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

const SHOWN: Record<NonNullable<Place>, string> = {
  project: "Projects",
  area: "Areas",
  resource: "Resources",
  archive: "Archive",
};

function hrefOf(p: Row): string {
  return p.about ? pageOf(p.about) : `/notes/${p.id}`;
}
