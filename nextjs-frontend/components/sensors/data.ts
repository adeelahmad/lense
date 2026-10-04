"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Sensors } from "@/app/openapi-client";
import type { HandlingChange, SensorUpdate } from "@/app/openapi-client/types.gen";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export function useSensors() {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["sensors"],
    queryFn: () => data(Sensors.listSensors({ client })),
    enabled: admin,
    // New devices show up as they first report.
    refetchInterval: 15_000,
  });
}

export function useSensor(id: number) {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["sensor", id],
    queryFn: () => data(Sensors.getSensor({ client, path: { sid: id } })),
    enabled: admin,
    refetchInterval: 15_000,
  });
}

export function useReadings(id: number, stream: string | null, limit = 100) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["sensor-readings", id, stream, limit],
    queryFn: () => data(Sensors.listReadings({ client, path: { sid: id }, query: { stream, limit } })),
    refetchInterval: 15_000,
  });
}

export function useSeries(id: number, stream: string, field: string | null, hours: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["sensor-series", id, stream, field, hours],
    queryFn: () => data(Sensors.getSeries({ client, path: { sid: id }, query: { stream, field, hours } })),
    enabled,
    staleTime: 60_000,
  });
}

export function usePatterns(id: number, label: string | null, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["sensor-patterns", id, label],
    queryFn: () => data(Sensors.listPatterns({ client, path: { sid: id }, query: { label } })),
    enabled,
    staleTime: 15_000,
  });
}

export function useLogins() {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["sensor-logins"],
    queryFn: () => data(Sensors.listLogins({ client })),
    enabled: admin,
  });
}

/** Change a sensor (name, status, namespace, handling, a bridge's connection), apply its suggestion, or remove it. */
export function useSensorActions() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = (id?: number) => {
    void qc.invalidateQueries({ queryKey: ["sensors"] });
    if (id != null) void qc.invalidateQueries({ queryKey: ["sensor", id] });
  };
  const update = useMutation({
    mutationFn: (v: { id: number; body: SensorUpdate; done?: string }) =>
      data(Sensors.updateSensor({ client, path: { sid: v.id }, body: v.body })),
    onSuccess: (_, v) => {
      refresh(v.id);
      if (v.done) toast({ tone: "green", title: v.done });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change the sensor", body: e.message }),
  });
  const suggestion = useMutation({
    mutationFn: (v: { id: number; name: string }) => data(Sensors.applySuggestion({ client, path: { sid: v.id } })),
    onSuccess: (_, v) => {
      refresh(v.id);
      toast({ tone: "green", title: "Suggestion applied", body: v.name });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t apply the suggestion", body: e.message }),
  });
  const review = useMutation({
    mutationFn: () => data(Sensors.reviewNew({ client })),
    onSuccess: (r) => {
      refresh();
      toast({ tone: "green", title: `Applied ${r.applied} suggestion${r.applied === 1 ? "" : "s"}` });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t apply the suggestions", body: e.message }),
  });
  const remove = useMutation({
    mutationFn: (v: { id: number; name: string }) => data(Sensors.deleteSensor({ client, path: { sid: v.id } })),
    onSuccess: (_, v) => {
      refresh();
      toast({ title: "Sensor removed", body: v.name });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t remove the sensor", body: e.message }),
  });
  return { update, suggestion, review, remove };
}

export type { HandlingChange };
