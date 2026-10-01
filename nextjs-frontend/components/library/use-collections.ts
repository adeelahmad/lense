"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Namespaces, Resources } from "@/app/openapi-client";
import type { CollectionMemberSet, CollectionNodeUpdate } from "@/app/openapi-client/types.gen";
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
      data(Resources.placeRecordings({ client, body: { recordings: v.recordings, collection: v.collection } })),
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

/** Who was given a role on a collection, and who has one through a collection it's inside (owners of the namespace
 * and admins of the collection may look). */
export function useCollectionMembers(ns: string, cid: number | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["collection-members", ns, cid],
    queryFn: () => data(Namespaces.listCollectionMembers({ client, path: { name: ns, cid: cid as number } })),
    enabled: cid != null,
    staleTime: 0,
  });
}

/** Give someone a role on a collection, change it, or take it away (role null). */
export function useSetCollectionMember(ns: string, cid: number | null) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: CollectionMemberSet) =>
      data(Namespaces.setCollectionMember({ client, path: { name: ns, cid: cid as number }, body })),
    onSuccess: (members, body) => {
      qc.setQueryData(["collection-members", ns, cid], members);
      void qc.invalidateQueries({ queryKey: collectionsKey(ns) });
      const who = body.email ?? members.find((m) => m.account === body.account)?.email ?? "They";
      toast({
        title: body.role
          ? `${who} is ${body.role === "viewer" ? "a viewer" : body.role === "editor" ? "an editor" : "an admin"} here now`
          : `Took away ${who}’s role`,
        tone: "green",
      });
    },
    onError: (e: unknown) =>
      toast({
        title: "Couldn’t change the role",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      }),
  });
}
