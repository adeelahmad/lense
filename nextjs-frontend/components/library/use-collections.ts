"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Namespaces, Recordings } from "@/app/openapi-client";
import type { CollectionNodeUpdate } from "@/app/openapi-client/types.gen";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

/** Query keys: a namespace's collection tree. */
export const collectionsKey = (ns: string | null) => ["collections-tree", ns] as const;

/** A namespace's collections, depth first and by name (none to load without a namespace). */
export function useCollectionTree(ns: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: collectionsKey(ns),
    queryFn: () => data(Namespaces.listNamespaceCollections({ client, path: { name: ns as string } })),
    enabled: Boolean(ns),
    staleTime: 0, // others arrange collections while the page is open
  });
}

/** Make, rename, move, describe, make default and delete a namespace's collections, and move recordings into one.
 * Each refreshes the tree and the recordings shown. */
export function useCollectionActions(ns: string | null) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const name = ns as string;
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: collectionsKey(ns) }),
      qc.invalidateQueries({ queryKey: ["recordings"] }),
      qc.invalidateQueries({ queryKey: ["recording"] }),
    ]);
  const create = useMutation({
    mutationFn: (body: { name: string; parent?: number | null; description?: string | null }) =>
      data(Namespaces.createNamespaceCollection({ client, path: { name }, body })),
    onSuccess: (c) => {
      void refresh();
      toast({ title: `Made “${c.name}”`, tone: "green" });
    },
    onError: fail("Couldn’t make the collection"),
  });
  const update = useMutation({
    mutationFn: (v: { cid: number; body: CollectionNodeUpdate }) =>
      data(Namespaces.updateNamespaceCollection({ client, path: { name, cid: v.cid }, body: v.body })),
    onSuccess: (c, v) => {
      void refresh();
      const b = v.body;
      toast({
        title: b.default
          ? `New recordings go to “${c.name}” now`
          : "parent" in b
            ? `Moved “${c.name}”`
            : b.name
              ? `Renamed to “${c.name}”`
              : `Saved “${c.name}”`,
        tone: "green",
      });
    },
    onError: fail("Couldn’t change the collection"),
  });
  const remove = useMutation({
    mutationFn: (v: { cid: number; name: string }) =>
      data(Namespaces.deleteNamespaceCollection({ client, path: { name, cid: v.cid } })),
    onSuccess: (_, v) => {
      void refresh();
      toast({ title: `Deleted “${v.name}”`, tone: "green" });
    },
    onError: fail("Couldn’t delete the collection"),
  });
  const place = useMutation({
    mutationFn: (v: { recordings: number[]; collection: number; name: string }) =>
      data(Recordings.placeRecordings({ client, body: { recordings: v.recordings, collection: v.collection } })),
    onSuccess: (r, v) => {
      void refresh();
      toast({
        title: r.moved ? `Moved ${plural(r.moved, "recording")} to “${v.name}”` : `Already in “${v.name}”`,
        tone: "green",
      });
    },
    onError: fail("Couldn’t move the recordings"),
  });
  return { create, update, remove, place };
}
