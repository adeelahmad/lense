"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Fields } from "@/app/openapi-client";
import type { FieldCreate, FieldUpdate, FieldValues } from "@/app/openapi-client/types.gen";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Query keys: a namespace's field definitions, and the values of one item. */
export const fk = {
  defs: (ns: string) => ["fields", ns] as const,
  values: (kind: "resource" | "collection" | "file", ...ids: (string | number)[]) =>
    ["field-values", kind, ...ids] as const,
};

/** A namespace's custom fields (its own, then those of the collections you see), or nothing without a namespace. */
export function useNamespaceFields(ns: string | null | undefined, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: fk.defs(ns ?? ""),
    queryFn: () => data(Fields.listFields({ client, path: { name: ns ?? "" } })),
    enabled: Boolean(ns) && enabled,
    staleTime: 30_000,
  });
}

/** Define, change and delete fields; each refreshes the namespace's list and the values shown. */
export function useFieldActions(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: fk.defs(ns) });
    void qc.invalidateQueries({ queryKey: ["field-values"] });
  };
  const create = useMutation({
    mutationFn: (body: FieldCreate) => data(Fields.createField({ client, path: { name: ns }, body })),
    onSuccess: (f) => {
      refresh();
      toast({ title: `Added “${f.label}”`, tone: "green" });
    },
    onError: fail("Couldn't add the field"),
  });
  const update = useMutation({
    mutationFn: (v: { fid: number; body: FieldUpdate }) =>
      data(Fields.updateField({ client, path: { name: ns, fid: v.fid }, body: v.body })),
    onSuccess: () => {
      refresh();
      toast({ title: "Saved", tone: "green" });
    },
    onError: fail("Couldn't change the field"),
  });
  const remove = useMutation({
    mutationFn: (fid: number) => data(Fields.deleteField({ client, path: { name: ns, fid } })),
    onSuccess: (f) => {
      refresh();
      toast({ title: `Deleted “${f.label}”`, tone: "green" });
    },
    onError: fail("Couldn't delete the field"),
  });
  return { create, update, remove };
}

/** How many items have a value for a field (asked before deleting it). */
export function useFieldUses(ns: string, fid: number | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["field-uses", ns, fid],
    queryFn: () => data(Fields.getField({ client, path: { name: ns, fid: fid ?? 0 } })),
    enabled: fid != null,
    staleTime: 0,
  });
}

/** Where an item's values come from and go to. */
export type ValuesSource =
  | { kind: "resource"; rid: number }
  | { kind: "collection"; ns: string; cid: number }
  | { kind: "file"; rid: number; fid: number };

const keyOf = (s: ValuesSource) =>
  s.kind === "resource"
    ? fk.values("resource", s.rid)
    : s.kind === "collection"
      ? fk.values("collection", s.ns, s.cid)
      : fk.values("file", s.rid, s.fid);

/** An item's custom fields with its values. */
export function useFieldValues(source: ValuesSource, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: keyOf(source),
    enabled,
    queryFn: (): Promise<FieldValues> =>
      source.kind === "resource"
        ? data(Fields.getResourceFields({ client, path: { rid: source.rid } }))
        : source.kind === "collection"
          ? data(Fields.getCollectionFields({ client, path: { name: source.ns, cid: source.cid } }))
          : data(Fields.getFileFields({ client, path: { rid: source.rid, fid: source.fid } })),
  });
}

/** Save an item's changed values. */
export function useSaveFieldValues(source: ValuesSource) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (values: Record<string, unknown>): Promise<FieldValues> => {
      const body = { values };
      return source.kind === "resource"
        ? data(Fields.saveResourceFields({ client, path: { rid: source.rid }, body }))
        : source.kind === "collection"
          ? data(Fields.saveCollectionFields({ client, path: { name: source.ns, cid: source.cid }, body }))
          : data(Fields.saveFileFields({ client, path: { rid: source.rid, fid: source.fid }, body }));
    },
    onSuccess: (out) => {
      qc.setQueryData(keyOf(source), out);
      if (source.kind === "resource") void qc.invalidateQueries({ queryKey: ["recording", source.rid] });
      toast({ title: "Fields saved", tone: "green" });
    },
    onError: (e: unknown) =>
      toast({
        title: "Couldn't save the fields",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      }),
  });
}
