"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Recordings } from "@/app/openapi-client";
import type { RecordingAccess, RecordingAccessUpdate } from "@/app/openapi-client/types.gen";
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
