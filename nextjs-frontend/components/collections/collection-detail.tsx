"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, FileText, MessagesSquare, Pencil, Play, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Collections } from "@/app/openapi-client";
import type { Collection } from "@/app/openapi-client/types.gen";
import { CollectionDialog } from "@/components/collections/collection-dialog";
import { describeCollection } from "@/components/collections/describe";
import { recordingHref } from "@/components/search/links";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, shortDate, tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

/** One collection: what it is, its recordings (newest 200 you can read), and what you can do with it. */
export function CollectionDetail({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { me, can } = useArchive();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const col = useQuery({
    queryKey: ["collection", id],
    queryFn: () => data(Collections.getCollection({ client, path: { cid: id } })),
  });
  const list = useQuery({
    queryKey: ["collections"],
    queryFn: () => data(Collections.listCollections({ client })),
    staleTime: 30_000,
  });
  const remove = useMutation({
    mutationFn: () => data(Collections.deleteCollection({ client, path: { cid: id } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections"] });
      toast({ title: "Collection deleted", tone: "intent" });
      router.push("/collections");
    },
  });
  if (col.isLoading) return <SkeletonRows rows={5} className="px-6 py-8" />;
  if (col.isError || !col.data)
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState
          tone="error"
          title={col.error?.message === "not found" ? "This collection isn’t here" : "Couldn’t open the collection"}
          actions={
            <Button asChild variant="secondary">
              <Link href="/collections">All collections</Link>
            </Button>
          }
        >
          {col.error?.message === "not found"
            ? "It may have been deleted, or it isn’t shared with you."
            : col.error?.message}
        </EmptyState>
      </div>
    );
  const c = col.data;
  const mine = c.account === me?.user.id;
  const full = (list.data ?? []).find((x) => x.id === id) as Collection | undefined;
  const canRun = can("editor");
  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-6">
      <Link
        href="/collections"
        className="inline-flex w-fit items-center gap-1 text-[13px] font-semibold text-fg-secondary hover:text-fg"
      >
        <ChevronLeft className="size-4" aria-hidden /> Collections
      </Link>
      <PageHeader
        className="mb-0"
        title={c.name}
        meta={`${count(c.count)} ${c.count === 1 ? "recording" : "recordings"} you can read`}
        actions={
          <>
            <Button asChild variant="secondary">
              <Link href={`/chat?collection=${id}`}>
                <MessagesSquare /> Chat with it
              </Link>
            </Button>
            <Button
              asChild={canRun && c.count > 0}
              variant="secondary"
              disabled={!canRun || c.count === 0}
              disabledReason={!canRun ? "Runs need editor access to a namespace" : "It has no recordings"}
              icon={canRun && c.count > 0 ? undefined : <Play />}
            >
              {canRun && c.count > 0 ? (
                <Link href={`/batches/new?collection=${id}`}>
                  <Play /> Run on {count(c.count)}
                </Link>
              ) : (
                `Run on ${count(c.count)}`
              )}
            </Button>
            <Button
              asChild={canRun && c.count > 0}
              variant="secondary"
              disabled={!canRun || c.count === 0}
              disabledReason={!canRun ? "Reports need editor access to a namespace" : "It has no recordings"}
              icon={canRun && c.count > 0 ? undefined : <FileText />}
            >
              {canRun && c.count > 0 ? (
                <Link href={`/collections/report?collection=${id}`}>
                  <FileText /> Report
                </Link>
              ) : (
                "Report"
              )}
            </Button>
            <Button
              variant="ghost"
              icon={<Pencil />}
              disabled={!mine || !full}
              disabledReason={mine ? undefined : "Only whoever saved it can change it"}
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
            <Button
              variant="danger-ghost"
              icon={<Trash2 />}
              disabled={!mine}
              disabledReason="Only whoever saved it can delete it"
              onClick={() => setDeleting(true)}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="m-0 mt-1 text-[13px] text-fg-secondary">
          {describeCollection(c, me?.user.id)}
          {c.description ? ` — ${c.description}` : ""}
        </p>
      </PageHeader>
      {c.recordings.length === 0 ? (
        <EmptyState title="No recordings you can read">
          {c.kind === "filter"
            ? "Nothing matches the filter yet. It keeps up to date as recordings arrive."
            : "The recordings in this list are in namespaces you can’t read."}
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Recordings in this collection">
            <THead className="border-t-0">
              <tr>
                <Th>Recording</Th>
                <Th className="w-[160px]">Recorded</Th>
                <Th className="w-[100px] text-right">Length</Th>
              </tr>
            </THead>
            <tbody>
              {c.recordings.map((r) => (
                <Tr key={r.id}>
                  <Td>
                    <Link
                      href={recordingHref(r.id)}
                      className="font-semibold text-fg hover:text-fg-accent hover:underline"
                    >
                      {r.title ?? `Recording ${r.id}`}
                    </Link>
                  </Td>
                  <Td className="text-fg-secondary">{shortDate(r.recorded_at)}</Td>
                  <Td className="tabular text-right text-fg-secondary">{r.duration_ms ? tc(r.duration_ms) : "—"}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
          {c.count > c.recordings.length && (
            <p className="m-0 border-t border-border px-4 py-2.5 text-[12.5px] text-fg-muted">
              Showing the newest {count(c.recordings.length)} of {count(c.count)}.
            </p>
          )}
        </div>
      )}
      {full && (
        <CollectionDialog
          key={`${full.id}-${full.updated_at}`}
          open={editing}
          onOpenChange={setEditing}
          editing={full}
        />
      )}
      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this collection?"
        description={`“${c.name}” goes; the recordings in it stay where they are.`}
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
              Delete
            </Button>
          </>
        }
      >
        {remove.isError && <Banner tone="error">{remove.error.message}</Banner>}
      </Dialog>
    </div>
  );
}
