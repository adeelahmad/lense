"use client";

import { useQuery } from "@tanstack/react-query";

import { Iiif, Metadata, Recordings } from "@/app/openapi-client";
import type { Meta, Problem } from "@/components/iiif/metadata-model";
import { data, useApiClient } from "@/lib/api/browser";

export type RecordingMeta = { meta: Meta; stored: Meta; defaults: Meta; problems: Problem[] };
export type NamespaceProfile = {
  required?: string[];
  defaults?: Meta;
  vocabularies?: { subjects?: string[]; language?: string[] };
  order?: string[];
  default_access?: string;
};
export type NamespaceMeta = { name?: string | null; meta: Meta; profile: NamespaceProfile };
export type RecordingBrief = { id: number; title?: string | null; namespace?: string | null; role?: string | null; speakers?: { id: number; name: string }[] };

export const keys = {
  meta: (rid: number) => ["metadata", rid] as const,
  history: (rid: number) => ["metadata-history", rid] as const,
  iiif: (rid: number) => ["iiif", rid] as const,
  recording: (rid: number) => ["iiif-recording", rid] as const,
  namespace: (ns: string) => ["namespace-metadata", ns] as const,
};

export function useRecordingMeta(rid: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: keys.meta(rid),
    queryFn: async () => (await data(Metadata.getRecordingMetadata({ client, path: { rid } }))) as unknown as RecordingMeta,
  });
}

export function useMetaHistory(rid: number) {
  const client = useApiClient();
  return useQuery({ queryKey: keys.history(rid), queryFn: () => data(Metadata.listRecordingMetadataHistory({ client, path: { rid } })) });
}

export function useRecordingIiif(rid: number) {
  const client = useApiClient();
  return useQuery({ queryKey: keys.iiif(rid), queryFn: () => data(Iiif.getRecordingIiif({ client, path: { rid } })) });
}

/** Title, namespace, your role and the speakers of a recording. */
export function useRecordingBrief(rid: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: keys.recording(rid),
    queryFn: async () => (await data(Recordings.getRecording({ client, path: { rid } }))) as unknown as RecordingBrief,
    staleTime: 60_000,
  });
}

export function useNamespaceMeta(ns: string | null | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: keys.namespace(ns ?? ""),
    queryFn: async () => (await data(Metadata.getNamespaceMetadata({ client, path: { name: ns as string } }))) as unknown as NamespaceMeta,
    enabled: Boolean(ns),
    staleTime: 60_000,
  });
}
