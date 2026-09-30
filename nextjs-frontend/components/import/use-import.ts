"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { Admin, Imports, Pipelines, Speakers } from "@/app/openapi-client";
import type { ImportPreview } from "@/app/openapi-client/types.gen";
import {
  DEFAULT_MAX_MB,
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
  | { type: "add"; files: File[]; maxMb: number }
  | { type: "remove"; id: string }
  | { type: "patch"; id: string; patch: Partial<Item> }
  | { type: "clear" };

let seq = 0;

function makeItem(file: File, maxMb: number): Item {
  const problem = localProblem(file, maxMb);
  return {
    id: `f${++seq}`,
    file,
    kind: kindOf(file.name),
    status: problem ? "blocked" : "reading",
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
      return [...items, ...a.files.filter((f) => !have.has(key(f))).map((f) => makeItem(f, a.maxMb))];
    }
    case "remove":
      return items.filter((i) => i.id !== a.id);
    case "patch":
      return items.map((i) => (i.id === a.id ? { ...i, ...a.patch } : i));
    case "clear":
      return [];
  }
}

/** The upload limit: from Settings for admins, else the server default (a 413 from the server corrects it). */
export function useMaxUploadMb(): number {
  const client = useApiClient();
  const { admin } = useArchive();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => data(Admin.getSettings({ client })),
    enabled: admin,
    staleTime: 5 * 60_000,
  });
  const mb = (settings.data as Record<string, { values?: Record<string, unknown> }> | undefined)?.server?.values
    ?.max_upload_mb;
  return typeof mb === "number" && mb > 0 ? mb : DEFAULT_MAX_MB;
}

/** The dropped files, each parsed by the server's preview (nothing is saved) as soon as it arrives, two at a time. */
export function useImportFiles() {
  const client = useApiClient();
  const maxMb = useMaxUploadMb();
  const [items, dispatch] = useReducer(reducer, []);
  const inflight = useRef(new Set<string>());

  const add = useCallback((files: File[]) => dispatch({ type: "add", files, maxMb }), [maxMb]);
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

  return { items, add, remove, patch, clear, maxMb };
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
    loading: q.isLoading,
  };
}

export type QueueItem = {
  key: string;
  name: string;
  title: string;
  namespace: string;
  kind: "file" | "paste";
  state: "uploading" | "sent" | "failed";
  recording?: number;
  job?: number;
  error?: string;
};

/** Sends imports one at a time; each row then follows its job. Leaving mid-upload asks first. */
export function useImportQueue() {
  const client = useApiClient();
  const qc = useQueryClient();
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const uploading = queue.some((q) => q.state === "uploading");

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

  const send = useCallback(
    async (
      jobs: {
        key: string;
        name: string;
        title: string;
        namespace: string;
        kind: "file" | "paste";
        body: () => Promise<Parameters<typeof Imports.importTranscript>[0]["body"]>;
      }[],
    ) => {
      setQueue((q) => [
        ...jobs.map((j) => ({
          key: j.key,
          name: j.name,
          title: j.title,
          namespace: j.namespace,
          kind: j.kind,
          state: "uploading" as const,
        })),
        ...q,
      ]);
      for (const j of jobs) {
        try {
          const res = await data(Imports.importTranscript({ client, body: await j.body() }));
          update(j.key, { state: "sent", recording: res.id, job: res.job });
          // New rows, counts and jobs: the library, the nav count and the activity pill all change.
          void qc.invalidateQueries({ queryKey: ["recordings"] });
          void qc.invalidateQueries({ queryKey: ["namespaces"] });
          void qc.invalidateQueries({ queryKey: ["jobs"] });
        } catch (e) {
          update(j.key, {
            state: "failed",
            error: e instanceof Error ? e.message : String(e),
          });
        }
      }
    },
    [client, qc],
  );

  return { queue, send, uploading, reset: () => setQueue([]) };
}

/** Build the import body for a file item. */
export async function fileBody(it: Item, namespace: string) {
  const labels = it.preview?.speakers ?? [];
  return {
    namespace,
    title: it.title.trim() || null,
    speakers: mappingParam(parseMapping(it.mapping, labels)),
    filename: it.file.name,
    data: await fileToBase64(it.file),
    format: "auto" as const,
  };
}
