"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GitMerge, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Entities } from "@/app/openapi-client";
import { Button } from "@/components/ui/button";
import { FindBox } from "@/components/ui/find-box";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count, plural } from "@/lib/format";

/**
 * Entity curation wherever entities show (the Entities page and its drawer, the graph, a recording): merging with an
 * Undo instead of a confirm, finding the entity to merge by typing, and the ones that may be the same thing.
 */

/** What shows an entity, so a merge, rename or hide is seen everywhere at once. */
export const ENTITY_KEYS = [
  ["entity"],
  ["entities"],
  ["entity-mentions"],
  ["entity-suggestions"],
  ["entity-find"],
  ["mentions"],
  ["graph"],
  ["recording"],
] as const;

export function useEntityRefresh(): () => void {
  const qc = useQueryClient();
  return useCallback(() => {
    for (const k of ENTITY_KEYS) void qc.invalidateQueries({ queryKey: [...k] });
  }, [qc]);
}

export type EntityPick = { id: number; name: string };
type Weighed = EntityPick & { mentions?: number | null; defined?: boolean | null };

/** Which of several entities to keep when they're merged: one on the fixed list first, then the most mentioned, then
 * the oldest (the lowest id). */
export function keepOf<T extends Weighed>(xs: T[]): T | undefined {
  return [...xs].sort(
    (a, b) =>
      Number(Boolean(b.defined)) - Number(Boolean(a.defined)) || (b.mentions ?? 0) - (a.mentions ?? 0) || a.id - b.id,
  )[0];
}

/** "Merged Acme Inc into Acme", "Merged 3 entities into Acme". */
export function mergeTitle(keep: string, others: string[]): string {
  return others.length === 1
    ? `Merged ${others[0]} into ${keep}`
    : `Merged ${plural(others.length, "entity", "entities")} into ${keep}`;
}

const why = (err: unknown) => (err instanceof ApiError ? err.message : "Please try again.");

/** Merge entities of one namespace into one, at once, with Undo on the toast. */
export function useMergeEntities() {
  const client = useApiClient();
  const toast = useToast();
  const refresh = useEntityRefresh();
  const [pending, setPending] = useState(false);
  // the toast outlives whatever opened it (a drawer closes, a node goes), so undo isn't tied to a mounted component
  const undo = useCallback(
    async (mid: number) => {
      try {
        await data(Entities.undoEntityMerge({ client, path: { mid } }));
        refresh();
        toast({ title: "Merge undone", tone: "green" });
      } catch (err) {
        toast({ title: "Couldn’t undo the merge", body: why(err), tone: "red" });
      }
    },
    [client, refresh, toast],
  );
  const merge = useCallback(
    async (keep: EntityPick, others: EntityPick[]): Promise<boolean> => {
      const rest = others.filter((o) => o.id !== keep.id);
      if (!rest.length) return false;
      setPending(true);
      try {
        const r = await data(
          Entities.mergeEntities({ client, body: { keep: keep.id, others: rest.map((o) => o.id) } }),
        );
        refresh();
        toast({
          title: mergeTitle(
            keep.name,
            rest.map((o) => o.name),
          ),
          body: "Their mentions and names are this one’s now.",
          tone: "green",
          duration: 10000,
          action: { label: "Undo", onClick: () => void undo(r.merge) },
        });
        return true;
      } catch (err) {
        toast({ title: "Couldn’t merge them", body: why(err), tone: "red" });
        return false;
      } finally {
        setPending(false);
      }
    },
    [client, refresh, toast, undo],
  );
  return { merge, pending };
}

/** Say entities of two namespaces are the same thing (they stay two; the graph joins them), with Undo on the toast. */
export function useLinkEntities() {
  const client = useApiClient();
  const toast = useToast();
  const refresh = useEntityRefresh();
  return useCallback(
    async (a: EntityPick, b: EntityPick): Promise<boolean> => {
      try {
        await data(Entities.linkEntity({ client, path: { eid: a.id }, body: { with: b.id } }));
        refresh();
        toast({
          title: `Linked ${a.name} and ${b.name}`,
          body: "They’re in different namespaces, so they stay two entities that the graph shows as the same thing.",
          tone: "green",
          duration: 10000,
          action: {
            label: "Undo",
            onClick: () =>
              void data(Entities.unlinkEntity({ client, path: { eid: a.id, other: b.id } }))
                .then(() => {
                  refresh();
                  toast({ title: "Link undone", tone: "green" });
                })
                .catch((err) => toast({ title: "Couldn’t undo the link", body: why(err), tone: "red" })),
          },
        });
        return true;
      } catch (err) {
        toast({ title: "Couldn’t link them", body: why(err), tone: "red" });
        return false;
      }
    },
    [client, refresh, toast],
  );
}

type Found = { id: number; name: string; type_label?: string; mentions?: number; builtin?: string | null };

/** Find an entity of a namespace by typing: the most mentioned first, arrow keys move, Enter picks, Escape leaves. */
export function EntityFind({
  ns,
  exclude = [],
  onPick,
  onCancel,
  placeholder = "Find an entity…",
  label,
  autoFocus,
  busy,
}: {
  ns: string;
  exclude?: number[];
  onPick: (e: EntityPick) => void;
  onCancel?: () => void;
  placeholder?: string;
  label: string;
  autoFocus?: boolean;
  busy?: boolean;
}) {
  const client = useApiClient();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 200);
    return () => clearTimeout(t);
  }, [q]);
  const found = useQuery({
    queryKey: ["entity-find", ns, debounced],
    queryFn: () =>
      data(
        Entities.listEntities({
          client,
          query: { namespaces: ns, q: debounced || undefined, limit: 8 + exclude.length, sort: "mentions" },
        }),
      ),
    staleTime: 30_000,
  });
  const items = ((found.data?.items ?? []) as unknown as Found[])
    .filter((e) => !e.builtin && !exclude.includes(e.id))
    .slice(0, 8)
    .map((e) => ({
      id: e.id,
      name: e.name,
      meta: `${e.type_label ?? "Entity"} · ${plural(e.mentions ?? 0, "mention")}`,
    }));
  return (
    <FindBox
      q={q}
      onQ={setQ}
      items={items}
      loading={found.isFetching || debounced !== q.trim()}
      empty={
        found.data ? (debounced ? `Nothing in ${ns} is called “${debounced}”.` : "No other entities here.") : undefined
      }
      onPick={(e) => onPick({ id: e.id, name: e.name })}
      onCancel={onCancel}
      placeholder={placeholder}
      label={label}
      autoFocus={autoFocus}
      busy={busy}
    />
  );
}

/** "Merge another entity into this one": a button that becomes a find box; the one picked is merged at once. */
export function MergeInto({
  keep,
  ns,
  open,
  onOpenChange,
}: {
  keep: EntityPick;
  ns: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { merge, pending } = useMergeEntities();
  if (!open) return null;
  return (
    <section
      aria-label={`Merge into ${keep.name}`}
      className="flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface p-3"
    >
      <div className="flex items-center gap-2">
        <GitMerge aria-hidden className="size-4 text-blue" />
        <h3 className="m-0 min-w-0 flex-1 text-[13px] font-bold text-fg-strong">
          Merge into <span className="text-fg">{keep.name}</span>
        </h3>
        <button
          type="button"
          aria-label="Stop merging"
          onClick={() => onOpenChange(false)}
          className="grid size-7 place-items-center rounded-full text-fg-muted hover:bg-background hover:text-fg"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <p className="m-0 text-[12.5px] text-fg-secondary">
        Pick the entity that is the same thing. Its mentions and names become {keep.name}’s, and you can undo it.
      </p>
      <EntityFind
        ns={ns}
        exclude={[keep.id]}
        label={`Entity to merge into ${keep.name}`}
        placeholder="Type a name…"
        autoFocus
        busy={pending}
        onCancel={() => onOpenChange(false)}
        onPick={(e) => void merge(keep, [e])}
      />
    </section>
  );
}

type Side = { id: number; name: string; type?: string; mentions?: number };
type Suggestion = { reason: string; confidence: number; a: Side; b: Side };

/** Entities that may be the same as this one (same letters, an acronym, a near spelling): merge one in, or say they
 * aren't the same so it isn't suggested again. */
export function MaybeSame({ entity, canEdit }: { entity: EntityPick; canEdit: boolean }) {
  const client = useApiClient();
  const toast = useToast();
  const refresh = useEntityRefresh();
  const { merge, pending } = useMergeEntities();
  const q = useQuery({
    queryKey: ["entity-suggestions", entity.id],
    queryFn: () => data(Entities.listEntitySuggestions({ client, query: { entity: entity.id, limit: 5 } })),
    enabled: canEdit,
    staleTime: 60_000,
  });
  if (!canEdit) return null;
  if (q.isLoading) return <Skeleton className="h-12 w-full" />;
  const items = (q.data ?? []) as unknown as Suggestion[];
  if (!items.length) return null;
  const notSame = async (other: Side) => {
    try {
      await data(Entities.markEntitiesNotSame({ client, body: { a: entity.id, b: other.id } }));
      refresh();
      toast({ title: `${entity.name} and ${other.name} aren’t the same`, body: "They won’t be suggested again." });
    } catch (err) {
      toast({ title: "Couldn’t change it", body: why(err), tone: "red" });
    }
  };
  return (
    <section className="flex flex-col gap-2">
      <h3 className="m-0 text-[13px] font-bold text-fg-strong">Maybe the same</h3>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {items.map((s) => {
          const other = s.a.id === entity.id ? s.b : s.a;
          return (
            <li key={other.id} className="flex min-h-8 flex-wrap items-center gap-2 text-[13px]">
              <span className="min-w-0 flex-1">
                <span className="font-semibold text-fg">{other.name}</span>
                <span className="text-fg-muted">
                  {" "}
                  · {s.reason} · {count(other.mentions ?? 0)} {other.mentions === 1 ? "mention" : "mentions"}
                </span>
              </span>
              <Button
                size="xs"
                variant="secondary"
                icon={<GitMerge />}
                disabled={pending}
                onClick={() => void merge(entity, [other])}
              >
                Merge
              </Button>
              <Button size="xs" variant="ghost" disabled={pending} onClick={() => void notSame(other)}>
                Not the same
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
