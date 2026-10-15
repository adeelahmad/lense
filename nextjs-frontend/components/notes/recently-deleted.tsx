"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Notes } from "@/app/openapi-client";
import { pageOf } from "@/components/notes/links";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/** The notes deleted in the last 30 days, under the Notes page's list: each goes back where it was with one click.
 * Deleting one for good now takes a second click on the same button (it can't be undone); otherwise they go on their
 * own after 30 days. Nothing shows while there are none. */
export function RecentlyDeleted({ ns }: { ns: string | null }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { can } = useArchive();
  const [sure, setSure] = useState<number | null>(null); // the one whose Delete now was clicked once
  const list = useQuery({
    queryKey: ["notes-deleted", ns],
    queryFn: () => data(Notes.deletedPages({ client, query: { ns } })),
  });
  const fail = (title: string) => (err: unknown) =>
    toast({ title, body: err instanceof ApiError ? err.message : "Please try again.", tone: "red" });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["notes-deleted"] });
    qc.invalidateQueries({ queryKey: ["notes-tree"] });
    qc.invalidateQueries({ queryKey: ["notes-search"] });
  };
  const putBack = useMutation({
    mutationFn: (pid: number) => data(Notes.restoreDeleted({ client, path: { pid } })),
    onSuccess: (p) => {
      refresh();
      router.push(p.about ? pageOf(p.about) : `/notes/${p.id}`);
    },
    onError: fail("Couldn’t put it back"),
  });
  const purge = useMutation({
    mutationFn: (pid: number) => data(Notes.purgeDeleted({ client, path: { pid } })),
    onSuccess: (_, pid) => {
      setSure(null);
      refresh();
      toast({ title: "Deleted for good", tone: "green" });
      qc.setQueryData(["notes-deleted", ns], (xs: { id: number }[] | undefined) => xs?.filter((x) => x.id !== pid));
    },
    onError: fail("Couldn’t delete it"),
  });
  const items = list.data ?? [];
  if (!items.length) return null;
  return (
    <details className="mt-6 rounded-md border border-border">
      <summary className="cursor-pointer px-4 py-2.5 text-[13px] font-bold text-fg-strong">
        Recently deleted <span className="font-normal text-fg-muted">{items.length}</span>
      </summary>
      <ul className="divide-y divide-border border-t border-border">
        {items.map((d) => {
          const editor = can("editor", d.namespace);
          return (
            <li key={`${d.namespace}:${d.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-[14px] font-bold text-fg">{d.title}</span>
                <span className="text-[12.5px] text-fg-muted">
                  {[
                    d.about ? `Page of ${d.about.split(":")[0]}` : null,
                    ns ? null : d.namespace,
                    `deleted ${relative(d.deleted_at)}`,
                    d.deleted_by ? `by ${d.deleted_by}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
              {editor && (
                <>
                  <Button
                    size="xs"
                    icon={<RotateCcw />}
                    onClick={() => putBack.mutate(d.id)}
                    disabled={putBack.isPending}
                  >
                    Put back
                  </Button>
                  <Button
                    size="xs"
                    variant="danger-ghost"
                    icon={<Trash2 />}
                    onClick={() => (sure === d.id ? purge.mutate(d.id) : setSure(d.id))}
                    onBlur={() => setSure((s) => (s === d.id ? null : s))}
                    disabled={purge.isPending}
                  >
                    {sure === d.id ? "Delete for good" : "Delete now"}
                  </Button>
                </>
              )}
            </li>
          );
        })}
      </ul>
      <p className="border-t border-border px-4 py-2 text-[12px] text-fg-muted">
        Deleted notes go for good after 30 days, with their files and history.
      </p>
    </details>
  );
}
