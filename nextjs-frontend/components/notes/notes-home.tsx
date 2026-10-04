"use client";

import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { FileText, NotebookPen, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Notes } from "@/app/openapi-client";
import { PLACES } from "@/components/notes/links";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { shortDate } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/** Every free note in view, the latest changed first, with where it's filed. */
export function NotesHome() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { namespace, namespaces, can } = useArchive();
  const names = namespace ? [namespace] : namespaces.map((n) => n.name);
  const trees = useQueries({
    queries: names.map((ns) => ({
      queryKey: ["notes-tree", ns],
      queryFn: () => data(Notes.listPages({ client, query: { ns } })),
    })),
  });
  const pages = trees
    .flatMap((t) => (t.data ? t.data.pages.map((p) => ({ ...p, ns: t.data.namespace })) : []))
    .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  const target = namespace ?? names.find((n) => can("editor", n)) ?? null;
  const create = useMutation({
    mutationFn: () => data(Notes.createPage({ client, body: { ns: target!, title: "Untitled" } })),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["notes-tree"] });
      router.push(`/notes/${p.id}`);
    },
    onError: (err) =>
      toast({
        title: "Couldn’t add a note",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      }),
  });
  const newNote =
    target && can("editor", target) ? (
      <Button variant="primary" icon={<Plus />} onClick={() => create.mutate()} disabled={create.isPending}>
        New note
      </Button>
    ) : null;
  const place = (v?: string | null) => PLACES.find((p) => p.value === v)?.label;

  return (
    <div className="mx-auto w-full max-w-[960px] px-6 py-8">
      <PageHeader title="Notes" meta={namespace ?? "All namespaces"} actions={newNote} />
      {trees.some((t) => t.isPending) ? (
        <SkeletonRows rows={5} />
      ) : !pages.length ? (
        <EmptyState icon={<NotebookPen />} title="No notes yet" actions={newNote}>
          The assistant writes notes as it learns, and you can write your own. Type @ in a note to link a recording,
          person or another note, and # for a topic. Every recording and entity also has a page of its own.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {pages.map((p) => (
            <li key={`${p.ns}:${p.id}`}>
              <Link href={`/notes/${p.id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-surface">
                {p.author === "assistant" ? (
                  <Sparkles className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-label="Written by the assistant" />
                ) : (
                  <FileText className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-hidden />
                )}
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-[14.5px] font-bold text-fg">{p.title}</span>
                  {p.summary && <span className="truncate text-[13px] text-fg-secondary">{p.summary}</span>}
                </span>
                <span className="shrink-0 text-right text-[12.5px] text-fg-muted">
                  {[place(p.place), !namespace ? p.ns : null, p.updated_at ? shortDate(p.updated_at) : null]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
