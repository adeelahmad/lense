"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Recordings } from "@/app/openapi-client";
import type { Permission, RecordingAccess, RecordingAccessUpdate } from "@/app/openapi-client/types.gen";
import { accessSummary } from "@/components/access/model";
import { keys as iiifKeys } from "@/components/iiif/queries";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

export const accessKey = (rid: number) => ["recording", rid, "access"] as const;

/** A recording's access: its level, open parts, featured, and the namespace's default. */
export function useRecordingAccess(rid: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: accessKey(rid),
    queryFn: () => data(Recordings.getRecordingAccess({ client, path: { rid } })),
    enabled,
  });
}

/** Save a recording's access (owners); refreshes everything that shows it and says what changed. */
export function useSaveAccess(rid: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: RecordingAccessUpdate) =>
      data(Recordings.updateRecordingAccess({ client, path: { rid }, body })),
    onSuccess: (r: RecordingAccess) => {
      qc.setQueryData(accessKey(rid), r);
      void qc.invalidateQueries({ queryKey: ["recording", rid] });
      void qc.invalidateQueries({ queryKey: ["recordings"] });
      void qc.invalidateQueries({ queryKey: iiifKeys.iiif(rid) });
      void qc.invalidateQueries({ queryKey: iiifKeys.meta(rid) });
      void qc.invalidateQueries({ queryKey: iiifKeys.history(rid) });
      toast({ title: "Access saved", body: accessSummary(r), tone: "green" });
    },
    onError: (e) => toast({ title: "Couldn’t change access", body: (e as Error).message, tone: "red" }),
  });
}

export const permissionsKey = (rid: number) => ["recording", rid, "permissions"] as const;

/** The people given permission on a recording (owners). */
export function usePermissions(rid: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: permissionsKey(rid),
    queryFn: () => data(Recordings.listRecordingPermissions({ client, path: { rid } })),
    enabled,
  });
}

/** Give someone with an account permission on a recording; the list comes back with them in it. */
export function useGivePermission(rid: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (email: string) =>
      data(Recordings.addRecordingPermission({ client, path: { rid }, body: { email: email.trim() } })),
    onSuccess: (list: Permission[], email) => {
      qc.setQueryData(permissionsKey(rid), list);
      toast({ title: "Permission given", body: `${email.trim()} sees all of it now.`, tone: "green" });
    },
  });
}

/** Take someone's permission on a recording away. */
export function useTakePermission(rid: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (p: Permission) =>
      data(Recordings.removeRecordingPermission({ client, path: { rid, account: p.account } })),
    onSuccess: (list: Permission[], p) => {
      qc.setQueryData(permissionsKey(rid), list);
      toast({ title: "Permission taken away", body: `${p.email} no longer sees what isn’t open to everyone.` });
    },
    onError: (e) => toast({ title: "Couldn’t take the permission away", body: (e as Error).message, tone: "red" }),
  });
}
