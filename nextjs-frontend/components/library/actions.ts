"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useSession } from "next-auth/react";
import { useCallback } from "react";

import { Jobs, Recordings } from "@/app/openapi-client";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

export const EXPORT_FORMATS = [
  { value: "txt", label: "Plain text (.txt)" },
  { value: "md", label: "Markdown (.md)" },
  { value: "srt", label: "Subtitles (.srt)" },
  { value: "vtt", label: "WebVTT (.vtt)" },
  { value: "json", label: "Lens JSON (.json)" },
] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number]["value"];

function filenameFrom(res: Response, fallback: string): string {
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  return m ? decodeURIComponent(m[1]) : fallback;
}

/** Fetch a file with the session token and hand it to the browser as a download. */
export async function downloadWithToken(url: string, token: string | undefined, fallbackName: string): Promise<void> {
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let msg = `Download failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") msg = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filenameFrom(res, fallbackName);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

/** Retry a failed job, reprocess recordings and export transcripts; each refreshes the library and says what happened. */
export function useRecordingActions() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const token = useSession().data?.accessToken;

  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    void qc.invalidateQueries({ queryKey: ["recordings"] });
  }, [qc]);

  const retryJob = useCallback(
    async (jid: number) => {
      try {
        await data(Jobs.retryJob({ client, path: { jid } }));
        toast({
          title: "Queued again",
          body: "It picks up from the step that failed.",
          tone: "intent",
        });
        refresh();
      } catch (e) {
        toast({
          title: "Couldn’t retry",
          body: (e as Error).message,
          tone: "red",
        });
      }
    },
    [client, refresh, toast],
  );

  /** Queue recordings again: their namespace's pipeline, or only these steps. */
  const reprocess = useCallback(
    async (recordings: number[], steps?: string[]) => {
      try {
        const res =
          recordings.length === 1
            ? {
                jobs: [
                  (
                    await data(
                      Recordings.reprocessRecording({
                        client,
                        path: { rid: recordings[0] },
                        body: steps?.length ? { steps } : undefined,
                      }),
                    )
                  ).job,
                ],
              }
            : await data(
                Jobs.createJobs({
                  client,
                  body: {
                    recordings,
                    steps: steps?.length ? steps : undefined,
                  },
                }),
              );
        toast({
          title: `${plural(res.jobs.length, "recording")} queued`,
          body: "Progress shows in the rows and in Activity.",
          tone: "intent",
        });
        refresh();
        return true;
      } catch (e) {
        toast({
          title: "Couldn’t queue that",
          body: (e as Error).message,
          tone: "red",
        });
        return false;
      }
    },
    [client, refresh, toast],
  );

  const exportMany = useCallback(
    async (ids: number[], fmt: ExportFormat) => {
      let done = 0;
      for (const id of ids) {
        try {
          await downloadWithToken(`/api/v1/recordings/${id}/export.${fmt}`, token, `recording-${id}.${fmt}`);
          done++;
        } catch (e) {
          toast({
            title: `Couldn’t export recording ${id}`,
            body: (e as Error).message,
            tone: "red",
          });
        }
      }
      if (done)
        toast({
          title: `Exported ${plural(done, "transcript")}`,
          tone: "green",
        });
    },
    [toast, token],
  );

  return { retryJob, reprocess, exportMany };
}
