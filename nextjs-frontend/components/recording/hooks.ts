"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import {
  Comments,
  Entities,
  Files,
  Jobs,
  Notes,
  Pipelines,
  Resources,
  Speakers,
  Templates,
  Video,
} from "@/app/openapi-client";
import type { CommentCreate, FileUpdate, HighlightCreate, NoteCreate, Recording } from "@/app/openapi-client/types.gen";
import type { FileRole } from "@/components/recording/files-model";
import { isActive, normalizeJob, visualNotes, type JobInfo } from "@/components/recording/jobs";
import { normalizePlayer } from "@/components/recording/model";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** Query keys: everything about one recording lives under ["recording", id] so one invalidation refreshes it all. */
export const rk = {
  all: (id: number) => ["recording", id] as const,
  detail: (id: number) => ["recording", id, "detail"] as const,
  player: (id: number) => ["recording", id, "player"] as const,
  jobs: (id: number) => ["recording", id, "jobs"] as const,
  edits: (id: number) => ["recording", id, "edits"] as const,
  outputs: (id: number) => ["recording", id, "outputs"] as const,
  entities: (id: number) => ["recording", id, "entities"] as const,
  notes: (id: number) => ["recording", id, "notes"] as const,
  comments: (id: number) => ["recording", id, "comments"] as const,
  highlights: (id: number) => ["recording", id, "highlights"] as const,
  files: (id: number) => ["recording", id, "files"] as const,
  fileLines: (id: number, fid: number) => ["recording", id, "files", fid, "lines"] as const,
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
    queryFn: () => data(Resources.getRecording({ client, path: { rid: id } })) as Promise<RecordingDetail>,
  });
}

export function usePlayer(id: number, poll: number | false = false) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.player(id),
    queryFn: () => data(Resources.getPlayer({ client, path: { rid: id } })),
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
    queryFn: () => data(Resources.listSegmentEdits({ client, path: { rid: id } })),
  });
}

export function useOutputs(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.outputs(id),
    queryFn: () => data(Resources.listOutputs({ client, path: { rid: id } })),
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

/** The namespace's speakers (for pickers and renames): only for people with a role in it, as speakers are the
 * namespace's and someone who sees just some of its collections isn't shown the rest. */
export function useSpeakerDirectory(ns: string | null | undefined) {
  const client = useApiClient();
  const { can } = useArchive();
  return useQuery({
    queryKey: ["speakers", ns],
    queryFn: () => data(Speakers.listSpeakers({ client, query: { ns: ns as string } })),
    enabled: Boolean(ns) && can("viewer", ns),
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

/** The namespace's face registry: only for people with a role in it (see useSpeakerDirectory). */
export function useNamespaceFaces(ns: string | null | undefined, enabled = true) {
  const client = useApiClient();
  const { can } = useArchive();
  return useQuery({
    queryKey: ["faces", ns],
    queryFn: () => data(Video.getNamespaceFaces({ client, path: { name: ns as string } })),
    enabled: Boolean(ns) && enabled && can("viewer", ns),
    staleTime: 30_000,
  });
}

/** Mutations shared by the page; each refreshes what it touched and reports failures in a toast. */
/** Your notes on the recording and the ones shared on it (the Notes tab, and the marks in the transcript). */
export function useNotes(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.notes(id),
    queryFn: () => data(Notes.listNotes({ client, path: { rid: id } })),
    staleTime: 0, // others share and delete notes while the page is open
  });
}

/** Write, change, share or unshare, and delete notes; each refreshes the list. */
export function useNoteActions(id: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => qc.invalidateQueries({ queryKey: rk.notes(id) });
  const create = useMutation({
    mutationFn: (body: NoteCreate) => data(Notes.createNote({ client, path: { rid: id }, body })),
    onSuccess: (n) => {
      void refresh();
      toast({ title: n.shared ? "Note shared" : "Note added", tone: "green" });
    },
    onError: fail("Couldn't add the note"),
  });
  const update = useMutation({
    mutationFn: (v: { nid: number; text?: string; shared?: boolean }) =>
      data(Notes.updateNote({ client, path: { rid: id, nid: v.nid }, body: { text: v.text, shared: v.shared } })),
    onSuccess: (n, v) => {
      void refresh();
      if (v.shared !== undefined)
        toast({ title: n.shared ? "Shared with everyone who can read it" : "Only you see it now", tone: "green" });
    },
    onError: fail("Couldn't change the note"),
  });
  const remove = useMutation({
    mutationFn: (nid: number) => data(Notes.deleteNote({ client, path: { rid: id, nid } })),
    onSuccess: () => {
      void refresh();
      toast({ title: "Note deleted", tone: "green" });
    },
    onError: fail("Couldn't delete the note"),
  });
  return { create, update, remove };
}

/** The resource's comments, threaded (the Comments tab, and the marks in the transcript). */
export function useComments(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.comments(id),
    queryFn: () => data(Comments.listComments({ client, path: { rid: id } })),
    staleTime: 0, // others comment, reply and resolve while the page is open
  });
}
/** Comment, reply, change or resolve, and delete; each refreshes the list. */
export function useCommentActions(id: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => qc.invalidateQueries({ queryKey: rk.comments(id) });
  const create = useMutation({
    mutationFn: (body: CommentCreate) => data(Comments.createComment({ client, path: { rid: id }, body })),
    onSuccess: (c) => {
      void refresh();
      toast({ title: c.parent != null ? "Reply added" : "Comment added", tone: "green" });
    },
    onError: fail("Couldn't add the comment"),
  });
  const update = useMutation({
    mutationFn: (v: { cid: number; text?: string; resolved?: boolean }) =>
      data(
        Comments.updateComment({ client, path: { rid: id, cid: v.cid }, body: { text: v.text, resolved: v.resolved } }),
      ),
    onSuccess: (c, v) => {
      void refresh();
      if (v.resolved !== undefined) toast({ title: c.resolved ? "Thread resolved" : "Thread reopened", tone: "green" });
    },
    onError: fail("Couldn't change the comment"),
  });
  const remove = useMutation({
    mutationFn: (cid: number) => data(Comments.deleteComment({ client, path: { rid: id, cid } })),
    onSuccess: () => {
      void refresh();
      void qc.invalidateQueries({ queryKey: ["flagged-comments"] });
      toast({ title: "Comment deleted", tone: "green" });
    },
    onError: fail("Couldn't delete the comment"),
  });
  const keep = useMutation({
    mutationFn: (cid: number) => data(Comments.keepComment({ client, path: { rid: id, cid } })),
    onSuccess: () => {
      void refresh();
      void qc.invalidateQueries({ queryKey: ["flagged-comments"] });
      toast({ title: "Kept", body: "It won’t be flagged again unless it changes.", tone: "green" });
    },
    onError: fail("Couldn't clear the flag"),
  });
  return { create, update, remove, keep };
}

/** The resource's highlights, by passage (the Highlights tab, and the marks on the text). */
export function useHighlights(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.highlights(id),
    queryFn: () => data(Comments.listHighlights({ client, path: { rid: id } })),
    staleTime: 0, // editors mark and change them while the page is open
  });
}
/** Mark a passage, change a highlight's colour or label, and delete one; each refreshes the list. */
export function useHighlightActions(id: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => qc.invalidateQueries({ queryKey: rk.highlights(id) });
  const create = useMutation({
    mutationFn: (body: HighlightCreate) => data(Comments.createHighlight({ client, path: { rid: id }, body })),
    onSuccess: () => {
      void refresh();
      toast({ title: "Highlighted", tone: "green" });
    },
    onError: fail("Couldn't highlight it"),
  });
  const update = useMutation({
    mutationFn: (v: { hid: number; colour?: HighlightCreate["colour"]; label?: string }) =>
      data(
        Comments.updateHighlight({ client, path: { rid: id, hid: v.hid }, body: { colour: v.colour, label: v.label } }),
      ),
    onSuccess: () => void refresh(),
    onError: fail("Couldn't change the highlight"),
  });
  const remove = useMutation({
    mutationFn: (hid: number) => data(Comments.deleteHighlight({ client, path: { rid: id, hid } })),
    onSuccess: () => {
      void refresh();
      toast({ title: "Highlight removed", tone: "green" });
    },
    onError: fail("Couldn't remove the highlight"),
  });
  return { create, update, remove };
}

/** The resource's primary file and supplementary files, with signed links to download them. */
export function useFiles(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: rk.files(id),
    queryFn: () => data(Files.listFiles({ client, path: { rid: id } })),
    staleTime: 60_000, // the download links are signed for a while; editors' changes refresh it
  });
}

const LINES_PAGE = 200;
/** The lines read from one file, a page at a time. */
export function useFileLines(id: number, fid: number, enabled = true) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: rk.fileLines(id, fid),
    enabled,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      data(Files.listFileLines({ client, path: { rid: id, fid }, query: { offset: pageParam, limit: LINES_PAGE } })),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.lines.length, 0);
      return loaded < last.total && last.lines.length ? loaded : undefined;
    },
  });
}

/** Add, change and delete files; each refreshes the list and search. */
export function useFileActions(id: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: rk.files(id) });
    for (const key of ["search-results", "search-base"]) void qc.invalidateQueries({ queryKey: [key] });
  };
  const add = useMutation({
    mutationFn: (v: { file: File; role: FileRole; language: string | null; label: string | null }) =>
      data(
        Files.addFile({
          client,
          path: { rid: id },
          query: { role: v.role, name: v.file.name, language: v.language, label: v.label },
          body: v.file,
        }),
      ),
    onSuccess: (f) => {
      refresh();
      toast({ title: `Added ${f.label || f.name}`, tone: "green" });
    },
    onError: fail("Couldn't add the file"),
  });
  const update = useMutation({
    mutationFn: (v: { fid: number; body: FileUpdate }) =>
      data(Files.updateFile({ client, path: { rid: id, fid: v.fid }, body: v.body })),
    onSuccess: (f) => {
      refresh();
      void qc.invalidateQueries({ queryKey: rk.fileLines(id, f.id) });
      toast({ title: "Saved", tone: "green" });
    },
    onError: fail("Couldn't change the file"),
  });
  const remove = useMutation({
    mutationFn: (fid: number) => data(Files.deleteFile({ client, path: { rid: id, fid } })),
    onSuccess: () => {
      refresh();
      toast({ title: "File deleted", tone: "green" });
    },
    onError: fail("Couldn't delete the file"),
  });
  return { add, update, remove };
}

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
      data(Resources.reprocessRecording({ client, path: { rid: id }, body })),
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
      return data(Resources.editSegment({ client, path: { rid: id, idx: v.idx }, body }));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: rk.player(id) });
      void qc.invalidateQueries({ queryKey: rk.edits(id) });
      void qc.invalidateQueries({ queryKey: rk.jobs(id) });
    },
    onError: fail("Couldn't save the change"),
  });
  // Lines are numbered: the next split or join waits until the transcript has its new numbers.
  const lineChanged = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: rk.player(id) }),
      qc.invalidateQueries({ queryKey: rk.edits(id) }),
      qc.invalidateQueries({ queryKey: rk.jobs(id) }),
    ]);
  const splitSegment = useMutation({
    mutationFn: (v: { idx: number; at: number; t?: number; speaker?: number | null }) =>
      data(
        Resources.splitSegment({
          client,
          path: { rid: id, idx: v.idx },
          body: {
            at: v.at,
            ...(v.t !== undefined ? { t: v.t } : {}),
            ...(v.speaker !== undefined ? { speaker: v.speaker } : {}),
          },
        }),
      ),
    onSuccess: lineChanged,
    onError: fail("Couldn't split the line"),
  });
  const mergeSegments = useMutation({
    mutationFn: (idx: number) => data(Resources.mergeSegments({ client, path: { rid: id, idx } })),
    onSuccess: lineChanged,
    onError: fail("Couldn't merge the lines"),
  });
  const rename = useMutation({
    mutationFn: (title: string) => data(Resources.updateRecording({ client, path: { rid: id }, body: { title } })),
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
    splitSegment,
    mergeSegments,
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
        Resources.exportRecording({
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

/**
 * What the last visual steps said: why text on screen wasn't read, whether faces found none, why objects weren't
 * looked for (from the latest job that ran them).
 */
export function useVisualNotes(jobs: JobInfo[]) {
  const last = jobs.find((j) => j.steps.some((s) => s.type === "ocr" || s.type === "faces" || s.type === "objects"));
  const detail = useJob(last?.id, isActive(last));
  const j = detail.data ?? last;
  return useMemo(() => visualNotes(j), [j]);
}
