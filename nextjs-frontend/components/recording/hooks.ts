"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { Entities, Jobs, Pipelines, Recordings, Speakers, Templates, Video } from "@/app/openapi-client";
import type { Recording } from "@/app/openapi-client/types.gen";
import { isActive, normalizeJob, type JobInfo } from "@/components/recording/jobs";
import { normalizePlayer } from "@/components/recording/model";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Query keys: everything about one recording lives under ["recording", id] so one invalidation refreshes it all. */
export const rk = {
  all: (id: number) => ["recording", id] as const,
  detail: (id: number) => ["recording", id, "detail"] as const,
  player: (id: number) => ["recording", id, "player"] as const,
  jobs: (id: number) => ["recording", id, "jobs"] as const,
  edits: (id: number) => ["recording", id, "edits"] as const,
  outputs: (id: number) => ["recording", id, "outputs"] as const,
  entities: (id: number) => ["recording", id, "entities"] as const,
  job: (jid: number) => ["job", jid] as const,
};

/** The recording row as the page sees it (the API returns extra fields beyond the typed ones). */
export type RecordingDetail = Recording & {
  source?: string | null;
  path?: string | null;
  remote?: { source?: number; path?: string } | null;
  engine?: string | null;
  diarizer?: string | null;
  language?: string | null;
  recorded_at?: string | null;
  duration_ms?: number | null;
  created_at?: string | null;
  transcribed_at?: string | null;
  diarized_at?: string | null;
  analyzed_at?: string | null;
  summarized_at?: string | null;
  fingerprint?: string | null;
  channels?: number | null;
  size?: number | null;
  error?: string | null;
  media?: {
    kind?: string;
    width?: number;
    height?: number;
    fps?: number;
    codec?: string;
    [k: string]: unknown;
  } | null;
};

export function useRecording(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.detail(id),
    queryFn: () => data(Recordings.getRecording({ client, path: { rid: id } })) as Promise<RecordingDetail>,
  });
}

export function usePlayer(id: number, poll: number | false = false) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.player(id),
    queryFn: () => data(Recordings.getPlayer({ client, path: { rid: id } })),
    select: normalizePlayer,
    refetchInterval: poll,
  });
}

/**
 * This recording's jobs, newest first. Polls every 3 s while one is queued or running; when it finishes, the whole
 * recording is refreshed and a toast says what happened.
 */
export function useRecordingJobs(id: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: rk.jobs(id),
    queryFn: async () =>
      (await data(Jobs.listJobs({ client, query: { recording: id, limit: 20 } }))).jobs.map(normalizeJob),
    refetchInterval: (query) => ((query.state.data ?? []).some(isActive) ? 3000 : 30_000),
  });
  const seen = useRef<Map<number, string>>(new Map());
  useEffect(() => {
    const jobs = q.data;
    if (!jobs) return;
    const prev = seen.current;
    let finished: JobInfo | null = null;
    for (const j of jobs) {
      const before = prev.get(j.id);
      if (before && isActive({ status: before }) && !isActive(j)) finished = j;
      prev.set(j.id, j.status);
    }
    if (finished) {
      void qc.invalidateQueries({ queryKey: rk.all(id) });
      if (finished.status === "succeeded")
        toast({
          title: "Processing finished",
          body: "Chapters, stats and the summary are up to date.",
          tone: "green",
        });
      else if (finished.status === "failed")
        toast({
          title: "A step failed",
          body: finished.error ?? "See the job in Activity.",
          tone: "red",
        });
    }
  }, [q.data, qc, id, toast]);
  return q;
}

/** One job with its log (for step times and notes in History). */
export function useJob(jid: number | null | undefined, live = false) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.job(jid ?? 0),
    queryFn: async () => normalizeJob(await data(Jobs.getJob({ client, path: { jid: jid as number } }))),
    enabled: Boolean(jid),
    refetchInterval: live ? 3000 : false,
  });
}

export function useEdits(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.edits(id),
    queryFn: () => data(Recordings.listSegmentEdits({ client, path: { rid: id } })),
  });
}

export function useOutputs(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.outputs(id),
    queryFn: () => data(Recordings.listOutputs({ client, path: { rid: id } })),
  });
}

export function useRecordingEntities(id: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.entities(id),
    queryFn: () => data(Entities.listEntities({ client, query: { recording: id, limit: 200 } })),
    enabled,
    staleTime: 60_000,
  });
}

export function useSpeakerDirectory(ns: string | null | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["speakers", ns],
    queryFn: () => data(Speakers.listSpeakers({ client, query: { ns: ns as string } })),
    enabled: Boolean(ns),
    staleTime: 30_000,
  });
}

export function usePipelines() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 60_000,
  });
}

export function useTemplates(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["templates"],
    queryFn: () => data(Templates.listTemplates({ client })),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useNamespaceFaces(ns: string | null | undefined, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["faces", ns],
    queryFn: () => data(Video.getNamespaceFaces({ client, path: { name: ns as string } })),
    enabled: Boolean(ns) && enabled,
    staleTime: 30_000,
  });
}

/** Mutations shared by the page; each refreshes what it touched and reports failures in a toast. */
export function useRecordingActions(id: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({
      title,
      body: e instanceof ApiError ? e.message : "Please try again.",
      tone: "red",
    });
  const refresh = () => qc.invalidateQueries({ queryKey: rk.all(id) });

  const reprocess = useMutation({
    mutationFn: (body: { steps?: (string | Record<string, unknown>)[]; pipeline?: number }) =>
      data(Recordings.reprocessRecording({ client, path: { rid: id }, body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: rk.jobs(id) }),
    onError: fail("Couldn't start the job"),
  });
  const retry = useMutation({
    mutationFn: (jid: number) => data(Jobs.retryJob({ client, path: { jid } })),
    onSuccess: () => {
      toast({
        title: "Retrying",
        body: "The job resumes from the step that failed.",
        tone: "intent",
      });
      void qc.invalidateQueries({ queryKey: rk.jobs(id) });
    },
    onError: fail("Couldn't retry the job"),
  });
  const editSegment = useMutation({
    mutationFn: (v: { idx: number; text?: string; speaker?: number | null }) => {
      const body: { text?: string; speaker?: number | null } = {};
      if (v.text !== undefined) body.text = v.text;
      if (v.speaker !== undefined) body.speaker = v.speaker;
      return data(Recordings.editSegment({ client, path: { rid: id, idx: v.idx }, body }));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rk.player(id) });
      void qc.invalidateQueries({ queryKey: rk.edits(id) });
      void qc.invalidateQueries({ queryKey: rk.jobs(id) });
    },
    onError: fail("Couldn't save the change"),
  });
  const rename = useMutation({
    mutationFn: (title: string) => data(Recordings.updateRecording({ client, path: { rid: id }, body: { title } })),
    onSuccess: (updated) => {
      qc.setQueryData(rk.detail(id), (old: RecordingDetail | undefined) => (old ? { ...old, ...updated } : old));
      void refresh();
      void qc.invalidateQueries({ queryKey: ["recordings"] });
      toast({ title: "Renamed", tone: "green" });
    },
    onError: fail("Couldn't rename the recording"),
  });
  const renameSpeaker = useMutation({
    mutationFn: (v: { sid: number; name: string }) =>
      data(
        Speakers.renameSpeaker({
          client,
          path: { sid: v.sid },
          body: { name: v.name },
        }),
      ),
    onSuccess: () => {
      void refresh();
      void qc.invalidateQueries({ queryKey: ["speakers"] });
    },
    onError: fail("Couldn't rename the speaker"),
  });
  const mergeSpeaker = useMutation({
    mutationFn: (v: { sid: number; into: number }) =>
      data(
        Speakers.mergeSpeaker({
          client,
          path: { sid: v.sid },
          body: { into: v.into },
        }),
      ),
    onSuccess: (r) => {
      void refresh();
      void qc.invalidateQueries({ queryKey: ["speakers"] });
      toast({
        title: "Speakers merged",
        tone: "green",
        action: {
          label: "Undo",
          onClick: () =>
            void data(Speakers.undoSpeakerMerge({ client, path: { mid: r.merge_id } }))
              .then(() => {
                void refresh();
                void qc.invalidateQueries({ queryKey: ["speakers"] });
              })
              .catch(fail("Couldn't undo the merge")),
        },
      });
    },
    onError: fail("Couldn't merge the speakers"),
  });
  return {
    reprocess,
    retry,
    editSegment,
    rename,
    renameSpeaker,
    mergeSpeaker,
    refresh,
  };
}

/** Download an export (txt, md, srt, vtt, json): it needs the session token, so fetch it and save the blob. */
export function useExport(id: number) {
  const client = useApiClient();
  const toast = useToast();
  return useMutation({
    mutationFn: async ({ fmt, title }: { fmt: string; title: string }) => {
      const blob = (await data(
        Recordings.exportRecording({
          client,
          path: { rid: id, fmt },
          parseAs: "blob",
        }),
      )) as unknown as Blob;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slug(title)}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    },
    onError: (e) =>
      toast({
        title: "Export failed",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      }),
  });
}

export function slug(s: string): string {
  return (
    s
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "recording"
  );
}
