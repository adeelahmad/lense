/**
 * A namespace's collections (GET /namespaces/{ns}/collections, depth first and by name): naming one by its path, what
 * may be done with one (delete, move inside another), and the options a picker shows. Pure functions; tested in
 * __tests__/library-collections.test.ts.
 */
import type { CollectionNode } from "@/app/openapi-client/types.gen";
import { plural } from "@/lib/format";

/** How deep collections go (the API refuses deeper). */
export const MAX_DEPTH = 8;

/** "Talks › 2024": a collection by its path from the top of its namespace. */
export function collectionPath(n: Pick<CollectionNode, "path" | "name">): string {
  return (n.path?.length ? n.path : [n.name]).join(" › ");
}

/** The collection's name, or null when it isn't one of these (deleted, or another namespace's). */
export function collectionName(
  nodes: CollectionNode[] | null | undefined,
  id: number | null | undefined,
): string | null {
  if (id == null) return null;
  return nodes?.find((n) => n.id === id)?.name ?? null;
}

/** The ids of a collection and every collection inside it. */
export function subtreeIds(nodes: CollectionNode[], id: number): Set<number> {
  const out = new Set<number>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const n of nodes)
      if (n.parent != null && out.has(n.parent) && !out.has(n.id)) {
        out.add(n.id);
        grew = true;
      }
  }
  return out;
}

/** How many levels a collection has below it (0 when nothing is inside it). */
export function height(nodes: CollectionNode[], id: number): number {
  const top = nodes.find((n) => n.id === id);
  if (!top) return 0;
  const ids = subtreeIds(nodes, id);
  return Math.max(0, ...nodes.filter((n) => ids.has(n.id)).map((n) => (n.depth ?? 0) - (top.depth ?? 0)));
}

/** Where a collection may move: inside any other collection that isn't inside it and keeps the tree at most
 * MAX_DEPTH deep. (The top of the namespace is always allowed.) */
export function moveTargets(nodes: CollectionNode[], id: number): CollectionNode[] {
  const inside = subtreeIds(nodes, id);
  const below = height(nodes, id);
  return nodes.filter((n) => !inside.has(n.id) && (n.depth ?? 0) + 2 + below <= MAX_DEPTH);
}

/** Why a collection can't be deleted (the default, or not empty), or null when it can. */
export function whyNoDelete(n: Pick<CollectionNode, "default" | "children" | "recordings">): string | null {
  if (n.default) return "This is the default collection: make another one the default first";
  if (n.children) return `It holds ${plural(n.children, "collection")}: move or delete them first`;
  if (n.recordings) return `It holds ${plural(n.recordings, "recording")}: move them to another collection first`;
  return null;
}

/** "3 recordings · 2 inside": what a collection holds, and what the collections inside it add. */
export function holds(n: Pick<CollectionNode, "recordings" | "total">): string {
  const own = n.recordings ?? 0;
  const inside = (n.total ?? own) - own;
  return [plural(own, "recording"), inside ? `${inside} inside` : null].filter(Boolean).join(" · ");
}

export type PickerOption = { value: string; label: string };

/** A picker's options: each collection indented by its depth, with " (default)" on the default. */
export function pickerOptions(nodes: CollectionNode[] | null | undefined): PickerOption[] {
  return (nodes ?? []).map((n) => ({
    value: String(n.id),
    label: `${" ".repeat(n.depth ?? 0)}${n.name}${n.default ? " (default)" : ""}`,
  }));
}

/** The Library showing a namespace, or one of its collections (and the ones inside it). */
export function libraryHref(ns: string, collection?: number | null): string {
  const q = new URLSearchParams({ namespace: ns });
  if (collection != null) q.set("collection", String(collection));
  return `/library?${q}`;
}

/** "podcasts › Talks › 2024": where a recording lives; `short` keeps the namespace and the collection it's in. */
export function homeText(
  ns: string | null | undefined,
  path: { name: string }[] | null | undefined,
  short = false,
): string | null {
  const names = (path ?? []).map((c) => c.name);
  const parts = [ns, ...(short ? names.slice(-1) : names)].filter(Boolean);
  return parts.length ? parts.join(" › ") : null;
}

/** The default collection's id, or null before the tree has loaded. */
export function defaultId(nodes: CollectionNode[] | null | undefined): number | null {
  return nodes?.find((n) => n.default)?.id ?? null;
}
