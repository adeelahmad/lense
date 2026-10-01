"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { Imports, Pipelines, Speakers, Uploads } from "@/app/openapi-client";
import type { ImportPreview, UploadLimits } from "@/app/openapi-client/types.gen";
import {
  DEFAULT_LIMITS,
  fileToBase64,
  initialMapping,
  kindOf,
  localProblem,
  mappingParam,
  parseMapping,
  readProblem,
  titleFromName,
  type FileKind,
  type Problem,
} from "@/components/import/files";
import { sendFile } from "@/components/import/uploader";
import { uploadError } from "@/components/import/upload-model";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export type ItemStatus = "reading" | "ready" | "attention" | "blocked";

export type Item = {
  id: string;
  file: File;
  kind: FileKind;
  status: ItemStatus;
  problem?: Problem;
  preview?: ImportPreview;
  title: string;
  mapping: string;
  mappingTouched: boolean;
};

type Action =
  | { type: "add"; files: File[]; limits: UploadLimits }
  | { type: "remove"; id: string }
  | { type: "patch"; id: string; patch: Partial<Item> }
  | { type: "clear" };

let seq = 0;

/** Audio and video are ready to upload as they are; transcripts are read first (the preview). */
export const isMedia = (kind: FileKind) => kind === "audio" || kind === "video";

function makeItem(file: File, limits: UploadLimits): Item {
  const problem = localProblem(file, limits);
  const kind = kindOf(file.name);
  return {
    id: `f${++seq}`,
    file,
    kind,
    status: problem ? "blocked" : isMedia(kind) ? "ready" : "reading",
    problem: problem ?? undefined,
    title: titleFromName(file.name),
    mapping: "",
    mappingTouched: false,
  };
}

function reducer(items: Item[], a: Action): Item[] {
  switch (a.type) {
    case "add": {
      // The same file dropped twice is one file.
      const key = (f: File) => `${f.name}|${f.size}|${f.lastModified}`;
      const have = new Set(items.map((i) => key(i.file)));
      return [...items, ...a.files.filter((f) => !have.has(key(f))).map((f) => makeItem(f, a.limits))];
    }
    case "remove":
      return items.filter((i) => i.id !== a.id);
    case "patch":
      return items.map((i) => (i.id === a.id ? { ...i, ...a.patch } : i));
    case "clear":
      return [];
  }
}

/** What the server accepts (GET /uploads/limits: Settings → Uploads, and the transcript limit), with the defaults
 * until it answers. */
export function useUploadLimits(): UploadLimits {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["upload-limits"],
    queryFn: () => data(Uploads.uploadLimits({ client })),
    staleTime: 5 * 60_000,
  });
  return q.data ?? DEFAULT_LIMITS;
}

/** Your uploads that stopped before the end: dropping the same file again carries on from there. */
export function useUnfinishedUploads(enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["uploads"],
    queryFn: () => data(Uploads.listUploads({ client })),
    enabled,
    staleTime: 30_000,
  });
}

/** The dropped files, each parsed by the server's preview (nothing is saved) as soon as it arrives, two at a time. */
export function useImportFiles() {
  const client = useApiClient();
  const limits = useUploadLimits();
  const [items, dispatch] = useReducer(reducer, []);
  const inflight = useRef(new Set<string>());

  const add = useCallback((files: File[]) => dispatch({ type: "add", files, limits }), [limits]);
  const remove = useCallback((id: string) => dispatch({ type: "remove", id }), []);
  const patch = useCallback((id: string, p: Partial<Item>) => dispatch({ type: "patch", id, patch: p }), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);

  useEffect(() => {
    const waiting = items.filter((i) => i.status === "reading" && !inflight.current.has(i.id));
    const slots = Math.max(0, 2 - inflight.current.size);
    for (const it of waiting.slice(0, slots)) {
      inflight.current.add(it.id);
      void (async () => {
        try {
          const b64 = await fileToBase64(it.file);
          const pv = await data(
            Imports.previewImport({
              client,
              body: { filename: it.file.name, data: b64, format: "auto" },
            }),
          );
          if (!pv.segments) {
            patch(it.id, {
              status: "attention",
              problem: readProblem("no transcript text found", it.file.name),
              preview: pv,
            });
          } else {
            patch(it.id, {
              status: "ready",
              preview: pv,
              title: pv.title?.trim() || titleFromName(it.file.name),
              mapping: initialMapping(pv.speakers),
            });
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          const tooBig = e instanceof ApiError && e.status === 413;
          patch(it.id, {
            status: tooBig ? "blocked" : "attention",
            problem: readProblem(msg, it.file.name),
          });
        } finally {
          inflight.current.delete(it.id);
          // Nudge the effect so the next waiting file starts.
          dispatch({ type: "patch", id: it.id, patch: {} });
        }
      })();
    }
  }, [items, client, patch]);

  return { items, add, remove, patch, clear, limits };
}

/** Debounced preview of pasted text. */
export function useTextPreview(text: string) {
  const client = useApiClient();
  const [debounced, setDebounced] = useState(text);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text), 500);
    return () => clearTimeout(t);
  }, [text]);
  return useQuery({
    queryKey: ["import-preview-text", debounced],
    queryFn: () =>
      data(
        Imports.previewImport({
          client,
          body: { text: debounced, format: "auto" },
        }),
      ),
    enabled: debounced.trim().length > 0,
    retry: false,
    staleTime: Infinity,
  });
}

/** Speakers that already exist in a namespace (to say which mapped names are new). */
export function useNamespaceSpeakers(ns: string | null) {
  const client = useApiClient();
  const { namespaces } = useArchive();
  const exists = Boolean(ns && namespaces.some((n) => n.name === ns));
  return useQuery({
    queryKey: ["speakers", ns],
    queryFn: () => data(Speakers.listSpeakers({ client, query: { ns: ns as string } })),
    enabled: exists,
    staleTime: 60_000,
  });
}

/** The pipeline a namespace runs after an import (its default, or the standard one). */
export function useNamespacePipeline(ns: string | null) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 5 * 60_000,
  });
  const own = q.data?.pipelines?.find((p) => (p.namespaces ?? []).includes(ns ?? ""));
  return {
    name: own ? own.name : "Standard pipeline",
    steps: q.data?.standard ?? [],
    /** Every saved pipeline: an import can run another one. */
    pipelines: q.data?.pipelines ?? [],
    loading: q.isLoading,
  };
}

export type QueueItem = {
  key: string;
  name: string;
  title: string;
  namespace: string;
  kind: "file" | "paste" | "media";
  state: "uploading" | "paused" | "sent" | "failed";
  /** Media: how much of the file has arrived, of its size. */
  sent?: number;
  size?: number;
  recording?: number;
  job?: number;
  /** Media: the namespace already had this file; `recording` is that one. */
  duplicate?: boolean;
  /** A transcript's own audio, attached once the transcript is in. */
  audio?: string;
  error?: string;
};

type ImportBody = Parameters<typeof Imports.importTranscript>[0]["body"];
export type QueueJob = {
  key: string;
  name: string;
  title: string;
  namespace: string;
} & (
  | { kind: "file" | "paste"; body: () => Promise<ImportBody>; audio?: File }
  | { kind: "media"; file: File; pipeline?: number | null; collection?: number | null }
);

/**
 * Sends imports one at a time; each row then follows its job. Audio and video go up in pieces, can be paused and
 * resumed, and carry on where they stopped after a dropped connection. Leaving mid-upload asks first.
 */
export function useImportQueue() {
  const client = useApiClient();
  const qc = useQueryClient();
  const limits = useUploadLimits();
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const uploading = queue.some((q) => q.state === "uploading");
  const media = useRef(
    new Map<
      string,
      {
        file: File;
        namespace: string;
        title: string;
        attach?: number;
        pipeline?: number | null;
        collection?: number | null;
      }
    >(),
  );
  const stops = useRef(new Map<string, AbortController>());

  useEffect(() => {
    if (!uploading) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  const update = (key: string, p: Partial<QueueItem>) =>
    setQueue((q) => q.map((x) => (x.key === key ? { ...x, ...p } : x)));

  // New rows, counts and jobs: the library, the nav count and the activity pill all change.
  const landed = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["recordings"] });
    void qc.invalidateQueries({ queryKey: ["namespaces"] });
    void qc.invalidateQueries({ queryKey: ["jobs"] });
  }, [qc]);

  const upload = useCallback(
    async (key: string) => {
      const m = media.current.get(key);
      if (!m || stops.current.has(key)) return;
      const stop = new AbortController();
      stops.current.set(key, stop);
      update(key, { state: "uploading", error: undefined });
      try {
        const up = await sendFile(client, m.file, {
          namespace: m.namespace,
          attach: m.attach,
          pipeline: m.pipeline,
          collection: m.collection,
          title: m.title,
          pieceMb: limits.chunk_mb,
          signal: stop.signal,
          onProgress: (u) => update(key, { sent: u.offset, size: u.size }),
        });
        update(key, {
          state: "sent",
          recording: up.recording ?? undefined,
          job: up.job ?? undefined,
          duplicate: up.duplicate,
        });
        landed();
      } catch (e) {
        if (stop.signal.aborted) update(key, { state: "paused" });
        else
          update(key, {
            state: "failed",
            error: uploadError(e instanceof ApiError ? e.status : 0, e instanceof Error ? e.message : String(e)),
          });
      } finally {
        stops.current.delete(key);
        void qc.invalidateQueries({ queryKey: ["uploads"] });
      }
    },
    [client, limits.chunk_mb, landed, qc],
  );

  const send = useCallback(
    async (jobs: QueueJob[]) => {
      setQueue((q) => [
        ...jobs.map((j) => ({
          key: j.key,
          name: j.name,
          title: j.title,
          namespace: j.namespace,
          kind: j.kind,
          state: "uploading" as const,
          size: j.kind === "media" ? j.file.size : undefined,
        })),
        ...q,
      ]);
      for (const j of jobs) {
        if (j.kind === "media") {
          media.current.set(j.key, {
            file: j.file,
            namespace: j.namespace,
            title: j.title,
            pipeline: j.pipeline,
            collection: j.collection,
          });
          await upload(j.key);
          continue;
        }
        try {
          const res = await data(Imports.importTranscript({ client, body: await j.body() }));
          if (j.audio) {
            // then its audio, attached to the transcript that just landed
            update(j.key, { recording: res.id, job: res.job, audio: j.audio.name, sent: 0, size: j.audio.size });
            media.current.set(j.key, { file: j.audio, namespace: j.namespace, title: j.title, attach: res.id });
            landed();
            await upload(j.key);
            continue;
          }
          update(j.key, { state: "sent", recording: res.id, job: res.job });
          landed();
        } catch (e) {
          update(j.key, {
            state: "failed",
            error: e instanceof Error ? e.message : String(e),
          });
        }
      }
    },
    [client, landed, upload],
  );

  /** Stop sending a file; what arrived stays on the server (for uploads.expire_hours) and Resume carries on. */
  const pause = useCallback((key: string) => stops.current.get(key)?.abort(), []);
  const resume = useCallback((key: string) => void upload(key), [upload]);

  return { queue, send, pause, resume, uploading, reset: () => setQueue([]) };
}

/** Build the import body for a file item (into `collection`, or the namespace's default when null). */
export async function fileBody(
  it: Item,
  namespace: string,
  pipeline: number | null = null,
  collection: number | null = null,
) {
  const labels = it.preview?.speakers ?? [];
  return {
    namespace,
    pipeline,
    collection,
    title: it.title.trim() || null,
    speakers: mappingParam(parseMapping(it.mapping, labels)),
    filename: it.file.name,
    data: await fileToBase64(it.file),
    format: "auto" as const,
  };
}
