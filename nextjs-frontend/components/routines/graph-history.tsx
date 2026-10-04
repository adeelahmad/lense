"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, ChevronDown, ChevronRight, History, Undo2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Graph2 as Graph } from "@/app/openapi-client";
import {
  diffLines,
  OPS,
  VIA,
  type Diff,
  type Page,
  type Rollback,
  type Version,
} from "@/components/routines/history-model";
import { RoutinesHeader } from "@/components/routines/routines-page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";

function EntityLinks({ v }: { v: Version }) {
  const ids = (v.entities ?? []).slice(0, 6);
  const more = (v.entities?.length ?? 0) - ids.length;
  return (
    <>
      {ids.map((id, i) => (
        <span key={id}>
          {i > 0 && ", "}
          <Link href={`/entities/${id}`} className="font-semibold text-fg hover:text-fg-accent hover:underline">
            {v.names?.[String(id)] ?? `#${id}`}
          </Link>
        </span>
      ))}
      {more > 0 && ` and ${more} more`}
    </>
  );
}

/** A diff as short lists: what was added, removed and changed. */
export function DiffView({ d }: { d: Diff }) {
  const rows = diffLines(d);
  if (!rows.length) return <p className="text-[12.5px] text-fg-muted">Nothing you can see changed.</p>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12.5px]">
      {rows.map((r) => (
        <div key={r.label} className="contents">
          <dt className="text-fg-muted">{r.label}</dt>
          <dd className="text-fg">
            {r.items.slice(0, 20).join("; ")}
            {r.items.length > 20 && ` and ${r.items.length - 20} more`}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function VersionDiff({ v }: { v: number }) {
  const client = useApiClient();
  const diff = useQuery({
    queryKey: ["graph-diff", v - 1, v],
    queryFn: () => data(Graph.graphDiff({ client, query: { from: String(v - 1), to: String(v) } })) as Promise<Diff>,
    staleTime: Infinity,
  });
  if (diff.isLoading) return <SkeletonRows rows={2} />;
  if (diff.error) return <p className="text-[12.5px] text-red-dark">{(diff.error as Error).message}</p>;
  return diff.data ? <DiffView d={diff.data} /> : null;
}

function NameDialog({ v, open, onOpenChange }: { v: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () => data(Graph.graphTag({ client, body: { name, version: String(v) } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["graph-history"] });
      toast({ tone: "green", title: `Version ${v} is “${name}”` });
      onOpenChange(false);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Name version ${v}`}
      description="A name to come back to, such as “before the cleanup”. Naming another version the same moves the name."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={!name.trim() || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <Field label="Name" error={save.isError ? save.error.message : undefined}>
        {({ id, invalid }) => (
          <Input id={id} value={name} invalid={invalid} maxLength={80} onChange={(e) => setName(e.target.value)} />
        )}
      </Field>
    </Dialog>
  );
}

function RollbackDialog({ v, open, onOpenChange }: { v: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const preview = useQuery({
    queryKey: ["graph-rollback", v],
    queryFn: () => data(Graph.graphRollback({ client, body: { to: String(v) } })) as Promise<Rollback>,
    enabled: open,
    staleTime: 0,
  });
  const run = useMutation({
    mutationFn: () =>
      data(Graph.graphRollback({ client, body: { to: String(v), dry_run: false } })) as Promise<Rollback>,
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["graph-history"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["graph-changes"] });
      toast({
        tone: "green",
        title: `Back to version ${v}`,
        body: r.skipped?.length
          ? `${r.skipped.length} couldn’t be taken back: ${r.skipped[0].why}`
          : `This is version ${r.version}; roll back to ${(r.version ?? 1) - 1} to undo it.`,
      });
      onOpenChange(false);
    },
  });
  const p = preview.data;
  return (
    <Dialog
      wide
      open={open}
      onOpenChange={onOpenChange}
      title={`Roll back to version ${v}`}
      description="Every change since then, in the namespaces you can edit, is taken back as one new version, so this can be undone too. Merges come undone with their mentions. What analysis found since stays."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            icon={<Undo2 />}
            onClick={() => run.mutate()}
            disabled={!p || !p.undo.length || run.isPending}
          >
            {run.isPending
              ? "Rolling back…"
              : `Take back ${p?.undo.length ?? ""} change${p?.undo.length === 1 ? "" : "s"}`}
          </Button>
        </>
      }
    >
      {preview.isLoading ? (
        <SkeletonRows rows={3} />
      ) : preview.error ? (
        <p className="text-[13px] text-red-dark">{(preview.error as Error).message}</p>
      ) : p && !p.undo.length ? (
        <p className="text-[13px] text-fg-secondary">Nothing you can edit changed since version {v}.</p>
      ) : p ? (
        <div className="flex flex-col gap-3">
          <DiffView d={p} />
          {p.kept > 0 && (
            <p className="text-[12.5px] text-fg-muted">
              {p.kept} analysis run{p.kept === 1 ? "" : "s"} since then stay{p.kept === 1 ? "s" : ""}.
            </p>
          )}
        </div>
      ) : null}
      {run.isError && <p className="text-[13px] text-red-dark">{run.error.message}</p>}
    </Dialog>
  );
}

function VersionRow({ v, head }: { v: Version; head: number }) {
  const { can } = useArchive();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<"name" | "rollback" | null>(null);
  const editor = can("editor");
  return (
    <li className="flex flex-col gap-2 border-b border-border py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <button
          type="button"
          aria-expanded={open}
          aria-label={open ? "Hide what changed" : "Show what changed"}
          onClick={() => setOpen(!open)}
          className="grid size-6 place-items-center rounded-sm text-fg-secondary hover:bg-surface-neutral"
        >
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <Badge mono>v{v.version}</Badge>
        <span className="min-w-0 text-fg">
          {OPS[v.op] ?? v.op} <EntityLinks v={v} />
        </span>
        {v.tags?.map((t) => (
          <Badge key={t} tone="intent">
            {t}
          </Badge>
        ))}
        <span className="flex-1" />
        <span className="text-[12px] text-fg-muted">
          {v.actor} · {VIA[v.via] ?? v.via} · <DateTime iso={v.at} />
        </span>
      </div>
      {v.why && <p className="pl-8 text-[12.5px] text-fg-secondary">{v.why}</p>}
      {open && (
        <div className="flex flex-col gap-3 pl-8">
          <VersionDiff v={v.version} />
          <div className="flex flex-wrap gap-2">
            <Button
              size="xs"
              variant="secondary"
              icon={<Bookmark />}
              disabled={!editor}
              disabledReason={editor ? undefined : needRole("editor")}
              onClick={() => setDialog("name")}
            >
              Name this version
            </Button>
            {v.version < head && (
              <Button
                size="xs"
                variant="secondary"
                icon={<Undo2 />}
                disabled={!editor}
                disabledReason={editor ? undefined : needRole("editor")}
                onClick={() => setDialog("rollback")}
              >
                Roll back to here
              </Button>
            )}
          </div>
        </div>
      )}
      {dialog === "name" && <NameDialog v={v.version} open onOpenChange={(o) => !o && setDialog(null)} />}
      {dialog === "rollback" && <RollbackDialog v={v.version} open onOpenChange={(o) => !o && setDialog(null)} />}
    </li>
  );
}

export function useGraphHistory(entity?: number) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: ["graph-history", entity ?? null],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) =>
      data(
        Graph.graphHistoryList({ client, query: { entity: entity ?? null, before: pageParam, limit: 50 } }),
      ) as Promise<Page>,
    getNextPageParam: (last) => last.next,
    staleTime: 10_000,
  });
}

/** Every change to the entity graph, newest first: who, through what, why and what changed, with naming a version
 * and rolling back to one. */
export function GraphHistoryPage({ entity }: { entity?: number }) {
  const history = useGraphHistory(entity);
  const pages = history.data?.pages ?? [];
  const head = pages[0]?.head ?? 0;
  const list = pages.flatMap((p) => p.versions);
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <RoutinesHeader tab="history" />
      <p className="text-[13px] text-fg-secondary">
        Every change to entities, their other names, links and “not the same” pairs is a version. The graph is at
        version {head}.
        {entity != null && (
          <>
            {" "}
            Showing the changes to one entity ·{" "}
            <Link href="/routines/history" className="font-semibold text-fg-accent hover:underline">
              Show all
            </Link>
          </>
        )}
      </p>
      {history.isLoading ? (
        <SkeletonRows rows={6} />
      ) : history.error ? (
        <EmptyState
          tone="error"
          icon={<History />}
          title="Couldn’t load the history"
          actions={<Button onClick={() => history.refetch()}>Try again</Button>}
        >
          {(history.error as Error).message}
        </EmptyState>
      ) : list.length ? (
        <>
          <ul className="flex flex-col rounded-md border border-border bg-surface px-4">
            {list.map((v) => (
              <VersionRow key={v.version} v={v} head={head} />
            ))}
          </ul>
          {history.hasNextPage && (
            <Button
              variant="secondary"
              className="self-start"
              disabled={history.isFetchingNextPage}
              onClick={() => history.fetchNextPage()}
            >
              {history.isFetchingNextPage ? "Loading…" : "Older changes"}
            </Button>
          )}
        </>
      ) : (
        <EmptyState icon={<History />} title="No changes yet">
          When analysis finds entities, or someone renames, merges or links them, each change shows here.
        </EmptyState>
      )}
    </div>
  );
}
