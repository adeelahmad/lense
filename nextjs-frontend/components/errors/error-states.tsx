"use client";

import { FileAudio, FileQuestion, ServerOff } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/browser";
import { bytes } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The centred error layout (Access AC6): icon tile, a mono code, a title, what happened and what to do next. */
export function ErrorState({
  icon,
  code,
  title,
  children,
  actions,
  bar,
  className,
  alert,
}: {
  icon: ReactNode;
  code?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  /** A strip across the top (the retry countdown). */
  bar?: ReactNode;
  className?: string;
  alert?: boolean;
}) {
  return (
    <div className={cn("flex flex-col overflow-hidden", className)}>
      {bar}
      <div
        role={alert ? "alert" : undefined}
        className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-10 text-center sm:p-8"
      >
        <span
          aria-hidden
          className="grid size-[52px] place-items-center rounded-[14px] bg-surface-neutral text-fg-secondary [&_svg]:size-[26px]"
        >
          {icon}
        </span>
        {code && <code className="font-mono text-[12px] font-medium text-fg-muted">{code}</code>}
        <h1 className="text-[20px] font-bold leading-[1.3] text-fg">{title}</h1>
        {children && <div className="max-w-[380px] text-[14px] leading-[1.55] text-fg-secondary">{children}</div>}
        {actions && <div className="mt-1 flex flex-wrap justify-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

/** 404: a page that doesn't exist, or a namespace you don't belong to (never revealed). */
export function NotFoundState({ className }: { className?: string }) {
  return (
    <ErrorState
      className={className}
      icon={<FileQuestion />}
      code="404"
      title="This page doesn’t exist"
      actions={
        <>
          <Button asChild variant="primary" size="sm">
            <Link href="/library">Go to Library</Link>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/search">Search</Link>
          </Button>
        </>
      }
    >
      The recording, speaker or page may have been deleted or moved, or the link has a typo. Namespaces you don’t belong
      to are never shown here.
    </ErrorState>
  );
}

/** Network failures and gateway errors: the browser or the proxy couldn't reach the archive server. */
export function isUnreachable(error: unknown): boolean {
  if (error instanceof ApiError)
    return error.status === 0 || error.status === 502 || error.status === 503 || error.status === 504;
  return error instanceof TypeError && /fetch|network/i.test(error.message);
}

const GATEWAY: Record<number, string> = {
  502: "502 Bad Gateway",
  503: "503 Service Unavailable",
  504: "504 Gateway Timeout",
};

/** The code line on the unreachable screen: the gateway's status when there was one. */
export function unreachableCode(error: unknown): string {
  return error instanceof ApiError ? (GATEWAY[error.status] ?? "network error") : "network error";
}

/** Seconds until the next automatic retry: 8, 16, 30, then every 30 s. */
export function retryDelay(attempt: number): number {
  return Math.min(30, 8 * 2 ** attempt);
}

/**
 * Can't reach the server (Access AC6): a red strip counts down to the next automatic retry, with "Retry now".
 * `onRetry` may return a promise; the countdown restarts after a failed retry.
 */
export function ServerUnreachable({
  onRetry,
  statusHref,
  className,
  error,
}: {
  onRetry: () => unknown;
  statusHref?: string;
  className?: string;
  error?: unknown;
}) {
  const [attempt, setAttempt] = useState(0);
  const [left, setLeft] = useState(retryDelay(0));
  const [retrying, setRetrying] = useState(false);
  const [host, setHost] = useState("the archive");
  const onRetryRef = useRef(onRetry);
  onRetryRef.current = onRetry;
  const attempts = useRef(0);

  useEffect(() => setHost(window.location.host), []);

  const retry = useCallback(async () => {
    setRetrying(true);
    try {
      await onRetryRef.current();
    } finally {
      attempts.current += 1;
      setAttempt(attempts.current);
      setLeft(retryDelay(attempts.current));
      setRetrying(false);
    }
  }, []);

  useEffect(() => {
    if (retrying) return;
    if (left <= 0) {
      void retry();
      return;
    }
    const t = setTimeout(() => setLeft((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [left, retrying, retry]);

  return (
    <ErrorState
      className={className}
      icon={<ServerOff />}
      code={unreachableCode(error)}
      title="Can’t reach Lens Archive"
      bar={
        <div
          role="alert"
          className="flex items-center gap-2.5 border-b border-red-border bg-red-surface px-4 py-2.5 text-[13px] font-medium leading-[1.3] text-fg"
        >
          <span aria-hidden className="font-extrabold text-red">
            ✕
          </span>
          <span className="flex-1" aria-live="off">
            {retrying ? "Can’t reach the server. Retrying…" : `Can’t reach the server. Retrying in ${left} s…`}
          </span>
          <button
            type="button"
            onClick={() => void retry()}
            disabled={retrying}
            className="font-bold text-blue hover:underline disabled:opacity-50"
          >
            Retry now
          </button>
        </div>
      }
      actions={
        <>
          <Button variant="primary" size="sm" onClick={() => void retry()} disabled={retrying}>
            Retry now
          </Button>
          {statusHref && (
            <Button asChild variant="ghost" size="sm">
              <Link href={statusHref}>Status</Link>
            </Button>
          )}
        </>
      }
    >
      The server at {host} isn’t responding (attempt {attempt + 1}). Background jobs keep running on the server; this
      page reconnects on its own.
    </ErrorState>
  );
}

/** An upload the server refused as too large (413): suggest a watched folder instead. */
export function FileTooLarge({
  name,
  size,
  limit,
  onRemove,
  className,
}: {
  name: string;
  size: number;
  limit: number;
  onRemove?: () => void;
  className?: string;
}) {
  return (
    <ErrorState
      className={className}
      icon={<FileAudio />}
      code={`413 · ${name}`}
      title={`File too large: ${bytes(size)} (limit ${bytes(limit)})`}
      actions={
        <>
          <Button asChild variant="primary" size="sm">
            <Link href="/sources?add=1">Use a source</Link>
          </Button>
          {onRemove && (
            <Button variant="ghost" size="sm" onClick={onRemove}>
              Remove
            </Button>
          )}
        </>
      }
    >
      Uploads over {bytes(limit)} are refused. Put the file in a watched folder instead (no size limit), or compress it
      to m4a or opus.
    </ErrorState>
  );
}
