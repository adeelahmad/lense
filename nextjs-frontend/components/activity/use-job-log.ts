"use client";

import { useSession } from "next-auth/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Jobs } from "@/app/openapi-client";
import { appendLog } from "@/components/activity/job-model";
import { data, useApiClient } from "@/lib/api/browser";
import { streamSSE } from "@/lib/api/sse";

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(t);
      resolve();
    });
  });
}

/**
 * A run's whole log (GET /jobs/{id}/log, page by page). While it runs, new lines come from the event stream
 * (`/events?logs=<id>`), or every 2 s when the stream can't be had; once it ends, the last lines are read. Null until
 * the first read.
 */
export function useJobLog(jobId: number, running: boolean): string[] | null {
  const client = useApiClient();
  const { data: session } = useSession();
  const token = session?.accessToken;
  const [lines, setLines] = useState<string[] | null>(null);
  const have = useRef<string[]>([]);

  const read = useCallback(
    async (after: number) => {
      let at = after;
      let out: string[] = [];
      for (;;) {
        const page = await data(Jobs.getJobLog({ client, path: { jid: jobId }, query: { after: at, limit: 5000 } }));
        out = out.concat(page.lines);
        at += page.lines.length;
        if (!page.more || !page.lines.length) return out;
      }
    },
    [client, jobId],
  );
  const add = useCallback((start: number, more: string[]) => {
    const next = appendLog(have.current, start, more);
    if (next && next !== have.current) {
      have.current = next;
      setLines(next);
    }
    return next != null;
  }, []);

  useEffect(() => {
    let off = false;
    have.current = [];
    void read(0)
      .then((all) => {
        if (off) return;
        have.current = all;
        setLines(all);
      })
      .catch(() => !off && setLines((l) => l ?? []));
    return () => {
      off = true;
    };
  }, [read]);

  useEffect(() => {
    if (!running || !token) return;
    const stop = new AbortController();
    const catchUp = async () => add(have.current.length, await read(have.current.length).catch(() => []));
    void (async () => {
      try {
        for await (const msg of streamSSE(`/api/v1/events?logs=${jobId}`, {
          accessToken: token,
          signal: stop.signal,
        })) {
          if (msg.event !== "log") continue;
          const ev = JSON.parse(msg.data) as { start: number; lines: string[] };
          if (!add(ev.start, ev.lines)) await catchUp();
        }
      } catch {
        // no stream: read the new lines every 2 s instead
      }
      while (!stop.signal.aborted) {
        await pause(2000, stop.signal);
        if (!stop.signal.aborted) await catchUp();
      }
    })();
    return () => {
      stop.abort();
      void catchUp(); // the lines written as it finished
    };
  }, [running, token, jobId, read, add]);

  return lines;
}
