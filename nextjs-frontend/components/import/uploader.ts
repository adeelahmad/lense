"use client";

import { Uploads } from "@/app/openapi-client";
import type { Client } from "@/app/openapi-client/client";
import type { Upload } from "@/app/openapi-client/types.gen";
import { nextPiece, resumeFrom, retryable, retryDelay } from "@/components/import/upload-model";
import { ApiError, data } from "@/lib/api/browser";

/** Tries for one piece before the upload stops (with "Try again" carrying on from there). */
const TRIES = 8;

export type SendOptions = {
  namespace: string;
  title?: string | null;
  pieceMb: number;
  signal: AbortSignal;
  onProgress: (u: Upload) => void;
};

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

/**
 * Upload a file in pieces (docs/api.md, Uploads). It carries on an unfinished upload of the same file into the same
 * namespace (after a reload, or "Pause"); a piece that fails is sent again, waiting longer each time; and whenever
 * the server and the browser disagree on how far it got, the server's word wins. Resolves with the finished upload
 * (its recording and job); aborting `signal` stops it, and what arrived stays for next time.
 */
export async function sendFile(client: Client, file: File, o: SendOptions): Promise<Upload> {
  const open = await data(Uploads.listUploads({ client, signal: o.signal }));
  let up =
    resumeFrom(open, file, o.namespace) ??
    (await data(
      Uploads.startUpload({
        client,
        signal: o.signal,
        body: {
          namespace: o.namespace,
          filename: file.name,
          size: file.size,
          title: o.title || null,
          modified: file.lastModified || null,
        },
      }),
    ));
  o.onProgress(up);
  let tries = 0;
  while (up.state !== "done") {
    const [start, end] = nextPiece(up.offset, up.size, o.pieceMb);
    try {
      up = await data(
        Uploads.sendChunk({
          client,
          signal: o.signal,
          path: { uid: up.id },
          query: { offset: start },
          body: file.slice(start, end),
        }),
      );
      tries = 0;
    } catch (e) {
      if (o.signal.aborted) throw e;
      const status = e instanceof ApiError ? e.status : 0;
      // 409: out of step, or another piece of it still arriving; ask where it got to
      if ((status !== 409 && !retryable(status)) || ++tries > TRIES) throw e;
      await wait(status === 409 ? 1000 : retryDelay(tries), o.signal);
      const now = up;
      up = await data(Uploads.getUpload({ client, signal: o.signal, path: { uid: now.id } })).catch(() => now);
    }
    o.onProgress(up);
  }
  return up;
}
