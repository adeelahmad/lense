"use client";

import { FileText, Loader2, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Upload } from "@/app/openapi-client/types.gen";
import { localProblem } from "@/components/import/files";
import { uploadError } from "@/components/import/upload-model";
import { sendFile } from "@/components/import/uploader";
import { useUploadLimits } from "@/components/import/use-import";
import { ApiError, useApiClient } from "@/lib/api/browser";
import { bytes } from "@/lib/format";
import { cn } from "@/lib/utils";

export type Attached = {
  key: string;
  file: File;
  state: "sending" | "ready" | "failed";
  sent: number;
  upload: Upload | null;
  error: string | null;
  /** Refused before sending (its type or size): trying again won't help. */
  refused?: boolean;
};

let seq = 0;

/**
 * Files sent with a chat message. Each starts uploading as soon as it's added (held: the assistant puts it in a
 * namespace), so it's there by the time the message goes.
 */
export function useAttachments() {
  const client = useApiClient();
  const limits = useUploadLimits();
  const [items, setItems] = useState<Attached[]>([]);
  const stops = useRef(new Map<string, AbortController>());
  const patch = (key: string, p: Partial<Attached>) =>
    setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...p } : x)));

  const start = useCallback(
    (key: string, file: File) => {
      const ctrl = new AbortController();
      stops.current.set(key, ctrl);
      patch(key, { state: "sending", error: null });
      sendFile(client, file, {
        namespace: "",
        hold: true,
        pieceMb: limits.chunk_mb,
        signal: ctrl.signal,
        onProgress: (u) => patch(key, { sent: u.offset }),
      })
        .then((u) => patch(key, { state: "ready", upload: u, sent: u.size }))
        .catch((e) => {
          if (ctrl.signal.aborted) return;
          patch(key, {
            state: "failed",
            error: uploadError(e instanceof ApiError ? e.status : 0, e instanceof Error ? e.message : String(e)),
          });
        });
    },
    [client, limits.chunk_mb],
  );

  const add = useCallback(
    (files: File[]) => {
      for (const file of files) {
        const key = `f${++seq}`;
        const problem = localProblem(file, limits);
        setItems((xs) => [
          ...xs,
          {
            key,
            file,
            state: problem ? "failed" : "sending",
            sent: 0,
            upload: null,
            error: problem?.title ?? null,
            refused: Boolean(problem),
          },
        ]);
        if (!problem) start(key, file);
      }
    },
    [limits, start],
  );
  const remove = useCallback((key: string) => {
    stops.current.get(key)?.abort();
    stops.current.delete(key);
    setItems((xs) => xs.filter((x) => x.key !== key));
  }, []);
  const retry = useCallback(
    (key: string) => {
      const it = items.find((x) => x.key === key);
      if (it) start(key, it.file);
    },
    [items, start],
  );
  /** The ready ones, taken out of the box to go with a message. */
  const take = useCallback(() => {
    const ready = items.filter((x) => x.state === "ready");
    setItems((xs) => xs.filter((x) => x.state !== "ready"));
    return ready;
  }, [items]);
  useEffect(() => () => stops.current.forEach((c) => c.abort()), []);

  return {
    items,
    add,
    remove,
    retry,
    take,
    sending: items.some((x) => x.state === "sending"),
    ready: items.some((x) => x.state === "ready"),
  };
}

/** The files waiting to go with the message: their progress, and a way to take one out. */
export function AttachmentChips({
  items,
  onRemove,
  onRetry,
}: {
  items: Attached[];
  onRemove: (key: string) => void;
  onRetry: (key: string) => void;
}) {
  if (!items.length) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Attached files">
      {items.map((x) => (
        <li
          key={x.key}
          className={cn(
            "flex max-w-[260px] items-center gap-1.5 rounded-md border px-2 py-1 text-[12.5px]",
            x.state === "failed" ? "border-red-border bg-red-surface" : "border-border bg-surface",
          )}
          title={x.error ?? x.file.name}
        >
          {x.state === "sending" ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-fg-muted" aria-hidden />
          ) : (
            <FileText className="size-3.5 shrink-0 text-fg-secondary" aria-hidden />
          )}
          <span className="min-w-0 truncate font-semibold text-fg">{x.file.name}</span>
          <span className="shrink-0 text-fg-muted">
            {x.state === "sending"
              ? `${Math.floor((100 * x.sent) / Math.max(1, x.file.size))}%`
              : x.state === "failed"
                ? "failed"
                : bytes(x.file.size)}
          </span>
          {x.state === "failed" && !x.refused && (
            <button type="button" aria-label={`Try ${x.file.name} again`} onClick={() => onRetry(x.key)}>
              <RotateCcw className="size-3.5 text-fg-secondary" />
            </button>
          )}
          <button type="button" aria-label={`Remove ${x.file.name}`} onClick={() => onRemove(x.key)}>
            <X className="size-3.5 text-fg-secondary" />
          </button>
        </li>
      ))}
    </ul>
  );
}

/** The files a sent message carried. */
export function SentFiles({ files }: { files: { filename: string; size: number }[] }) {
  if (!files.length) return null;
  return (
    <ul className="flex flex-wrap justify-end gap-1.5 self-end" aria-label="Files sent">
      {files.map((f, i) => (
        <li
          key={`${f.filename}${i}`}
          className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-[12.5px]"
        >
          <FileText className="size-3.5 text-fg-secondary" aria-hidden />
          <span className="max-w-[220px] truncate font-semibold text-fg">{f.filename}</span>
          <span className="text-fg-muted">{bytes(f.size)}</span>
        </li>
      ))}
    </ul>
  );
}
