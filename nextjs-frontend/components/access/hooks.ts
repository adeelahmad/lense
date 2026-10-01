"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Namespaces, Resources } from "@/app/openapi-client";
import type {
  AccessRequest,
  IpGroup,
  IpGroupCreate,
  IpGroups,
  Permission,
  RecordingAccess,
  RecordingAccessUpdate,
  RecordingIpGroup,
} from "@/app/openapi-client/types.gen";
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
    queryFn: () => data(Resources.getRecordingAccess({ client, path: { rid } })),
    enabled,
  });
}

/** Save a recording's access (owners); refreshes everything that shows it and says what changed. */
export function useSaveAccess(rid: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: RecordingAccessUpdate) => data(Resources.updateRecordingAccess({ client, path: { rid }, body })),
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
    queryFn: () => data(Resources.listRecordingPermissions({ client, path: { rid } })),
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
      data(Resources.addRecordingPermission({ client, path: { rid }, body: { email: email.trim() } })),
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
      data(Resources.removeRecordingPermission({ client, path: { rid, account: p.account } })),
    onSuccess: (list: Permission[], p) => {
      qc.setQueryData(permissionsKey(rid), list);
      toast({ title: "Permission taken away", body: `${p.email} no longer sees what isn’t open to everyone.` });
    },
    onError: (e) => toast({ title: "Couldn’t take the permission away", body: (e as Error).message, tone: "red" }),
  });
}

export const requestsKey = (rid: number) => ["recording", rid, "requests"] as const;

/** Requests for access to a recording (owners). */
export function useAccessRequests(rid: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: requestsKey(rid),
    queryFn: () => data(Resources.listAccessRequests({ client, path: { rid } })),
    enabled,
  });
}

/** Approve (which gives permission) or decline a request. */
export function useDecideRequest(rid: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ req, approve }: { req: AccessRequest; approve: boolean }) =>
      data(
        (approve ? Resources.approveAccessRequest : Resources.declineAccessRequest)({
          client,
          path: { rid, account: req.account },
        }),
      ),
    onSuccess: (list: AccessRequest[], { req, approve }) => {
      qc.setQueryData(requestsKey(rid), list);
      void qc.invalidateQueries({ queryKey: permissionsKey(rid) });
      void qc.invalidateQueries({ queryKey: ["access-requests"] });
      toast(
        approve
          ? { title: "Request approved", body: `${req.email} sees all of it now.`, tone: "green" }
          : { title: "Request declined", body: `${req.email} can ask again.` },
      );
    },
    onError: (e) => toast({ title: "Couldn’t answer the request", body: (e as Error).message, tone: "red" }),
  });
}

export const ipGroupsKey = (ns: string) => ["namespace", ns, "ip-groups"] as const;

/** A namespace's IP groups, and the address the server sees for you (owners). */
export function useIpGroups(ns: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ipGroupsKey(ns),
    queryFn: () => data(Namespaces.listIpGroups({ client, path: { name: ns } })),
    enabled,
  });
}

/** Add an IP group, or change one (with its id). The list comes back with it. */
export function useSaveIpGroup(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: IpGroupCreate }) =>
      data(
        id == null
          ? Namespaces.createIpGroup({ client, path: { name: ns }, body })
          : Namespaces.updateIpGroup({ client, path: { name: ns, gid: id }, body }),
      ),
    onSuccess: (list: IpGroups, { id, body }) => {
      qc.setQueryData(ipGroupsKey(ns), list);
      void qc.invalidateQueries({ queryKey: ["recording"], predicate: (q) => q.queryKey[2] === "ip-groups" });
      toast({ title: id == null ? "IP group added" : "IP group saved", body: body.name, tone: "green" });
    },
  });
}

/** Delete an IP group: visitors from its addresses lose what it opened. */
export function useDeleteIpGroup(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (g: IpGroup) => data(Namespaces.deleteIpGroup({ client, path: { name: ns, gid: g.id } })),
    onSuccess: (list: IpGroups, g) => {
      qc.setQueryData(ipGroupsKey(ns), list);
      void qc.invalidateQueries({ queryKey: ["recording"], predicate: (q) => q.queryKey[2] === "ip-groups" });
      toast({ title: "IP group deleted", body: `${g.name} no longer opens anything.` });
    },
    onError: (e) => toast({ title: "Couldn’t delete the IP group", body: (e as Error).message, tone: "red" }),
  });
}

export const recordingIpGroupsKey = (rid: number) => ["recording", rid, "ip-groups"] as const;

/** The namespace's IP groups, each with whether it opens this recording (owners). */
export function useRecordingIpGroups(rid: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: recordingIpGroupsKey(rid),
    queryFn: () => data(Resources.listRecordingIpGroups({ client, path: { rid } })),
    enabled,
  });
}

/** Open the recording to an IP group that opens chosen recordings, or close it again. */
export function useChooseIpGroup(rid: number, ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ g, on }: { g: RecordingIpGroup; on: boolean }) =>
      data(
        (on ? Resources.openRecordingToIpGroup : Resources.closeRecordingToIpGroup)({
          client,
          path: { rid, gid: g.id },
        }),
      ),
    onSuccess: (list: RecordingIpGroup[], { g, on }) => {
      qc.setQueryData(recordingIpGroupsKey(rid), list);
      void qc.invalidateQueries({ queryKey: ipGroupsKey(ns) });
      toast(
        on
          ? { title: `Open to ${g.name}`, body: "Visitors from its addresses see all of it.", tone: "green" }
          : { title: `Closed to ${g.name}` },
      );
    },
    onError: (e) => toast({ title: "Couldn’t change the IP group", body: (e as Error).message, tone: "red" }),
  });
}
