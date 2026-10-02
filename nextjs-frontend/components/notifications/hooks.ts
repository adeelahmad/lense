"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Notifications } from "@/app/openapi-client";
import type {
  NotifyTarget,
  NotifyTargetCreate,
  NotifyTargetCreated,
  NotifyTargetUpdate,
  NotifyTargets,
  NotifyTestResult,
} from "@/app/openapi-client/types.gen";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

export const notifyKey = (ns: string) => ["namespace", ns, "notifications"] as const;
export const deliveriesKey = (ns: string, id: number) => ["namespace", ns, "notifications", id, "deliveries"] as const;

/** A namespace's notification targets and the events they can get (owners). */
export function useNotifyTargets(ns: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: notifyKey(ns),
    queryFn: () => data(Notifications.listNotifyTargets({ client, path: { name: ns } })),
    enabled,
  });
}

/** Add a target. A webhook comes back with its signing secret, shown once. */
export function useCreateTarget(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: NotifyTargetCreate) =>
      data(Notifications.createNotifyTarget({ client, path: { name: ns }, body })),
    onSuccess: (r: NotifyTargetCreated) => {
      void qc.invalidateQueries({ queryKey: notifyKey(ns) });
      toast({ title: "Target added", body: r.target.name, tone: "green" });
    },
  });
}

/** Change a target. */
export function useUpdateTarget(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: NotifyTargetUpdate }) =>
      data(Notifications.updateNotifyTarget({ client, path: { name: ns, tid: id }, body })),
    onSuccess: (t: NotifyTarget) => {
      qc.setQueryData(notifyKey(ns), (old: NotifyTargets | undefined) =>
        old ? { ...old, targets: old.targets.map((x) => (x.id === t.id ? t : x)) } : old,
      );
      toast({ title: "Target saved", body: t.name, tone: "green" });
    },
  });
}

/** Delete a target and what it was sent. */
export function useDeleteTarget(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (t: NotifyTarget) => data(Notifications.deleteNotifyTarget({ client, path: { name: ns, tid: t.id } })),
    onSuccess: (_r, t) => {
      void qc.invalidateQueries({ queryKey: notifyKey(ns) });
      toast({ title: "Target deleted", body: `${t.name} gets no more notifications.` });
    },
    onError: (e) => toast({ title: "Couldn’t delete the target", body: (e as Error).message, tone: "red" }),
  });
}

/** Send a target a test message now, and say how it went. */
export function useTestTarget(ns: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (t: NotifyTarget) => data(Notifications.testNotifyTarget({ client, path: { name: ns, tid: t.id } })),
    onSuccess: (r: NotifyTestResult, t) => {
      void qc.invalidateQueries({ queryKey: notifyKey(ns) });
      void qc.invalidateQueries({ queryKey: deliveriesKey(ns, t.id) });
      toast(
        r.ok
          ? { title: "Test sent", body: `${t.name} answered ${r.code}.`, tone: "green" }
          : { title: "The test didn’t get through", body: r.error ?? undefined, tone: "red" },
      );
    },
    onError: (e) => toast({ title: "Couldn’t send the test", body: (e as Error).message, tone: "red" }),
  });
}

/** A webhook's new signing secret, shown once. */
export function useRotateSecret(ns: string) {
  const client = useApiClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (t: NotifyTarget) => data(Notifications.rotateNotifySecret({ client, path: { name: ns, tid: t.id } })),
    onError: (e) => toast({ title: "Couldn’t make a new secret", body: (e as Error).message, tone: "red" }),
  });
}

/** What a target was sent lately, newest first. */
export function useDeliveries(ns: string, id: number | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: deliveriesKey(ns, id ?? 0),
    queryFn: () => data(Notifications.listNotifyDeliveries({ client, path: { name: ns, tid: id ?? 0 } })),
    enabled: id != null,
  });
}
