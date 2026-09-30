"use client";

import { Download } from "lucide-react";
import { useEffect, useRef } from "react";

import type { LogLine, LogTone } from "@/components/activity/job-model";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const TONE: Record<LogTone, string> = {
  out: "text-term-muted",
  info: "text-[var(--term-blue)]",
  ok: "text-[var(--term-green)]",
  fail: "text-[var(--term-red)]",
  gate: "text-[var(--term-gold)]",
};

/** Download lines as a text file (the full log the server keeps). */
export function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * The log viewer: a terminal (Aladdin) with a toolbar. Streaming while the step runs (it follows the end unless you
 * scroll up), then "Streaming stopped". The server keeps the last 200 lines of each run.
 */
export function LogViewer({
  heading,
  title,
  lines,
  live,
  finished,
  empty = "No log lines yet.",
  onDownload,
  truncated,
  className,
}: {
  heading: string;
  title: string;
  lines: LogLine[];
  live?: boolean;
  finished?: boolean;
  empty?: string;
  onDownload?: () => void;
  truncated?: boolean;
  className?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <section aria-label={heading} className={cn("flex min-h-0 flex-col gap-2.5", className)}>
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="flex-1 text-[13px] font-bold text-fg">{heading}</h2>
        <span className="flex items-center gap-1.5 text-[12px] font-medium text-fg-muted">
          {live && <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-blue" />}
          {live ? "Streaming · updates every 2 s" : finished ? "Streaming stopped · run finished" : "Streams here once it starts"}
          {truncated ? " · last 200 lines" : ""}
        </span>
        {onDownload && (
          <Button variant="ghost" size="sm" icon={<Download />} onClick={onDownload} disabled={!lines.length} disabledReason="Nothing logged yet">
            Download
          </Button>
        )}
      </div>
      <div className="flex min-h-[180px] flex-col overflow-hidden rounded-md bg-term-bg font-mono text-[13px] leading-[1.6] text-term-fg">
        <div className="flex items-center gap-1.5 border-b border-white/10 px-3.5 py-2.5">
          {["#EA4335", "#F9AB00", "#34A853"].map((c) => (
            <span key={c} aria-hidden className="size-2.5 rounded-full opacity-85" style={{ background: c }} />
          ))}
          <span className="ml-2 truncate text-[12px] text-term-muted">{title}</span>
        </div>
        <div
          ref={scroller}
          role="log"
          aria-live="off"
          aria-label={`${heading} log`}
          tabIndex={0}
          onScroll={(e) => {
            const el = e.currentTarget;
            follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
          className="max-h-[420px] min-h-[120px] overflow-auto px-4 py-3 focus-visible:outline-offset-[-2px]"
        >
          {lines.length ? (
            <pre className="m-0 whitespace-pre-wrap break-words font-mono">
              {lines.map((l, i) => (
                <div key={i} className={TONE[i === 0 && l.tone === "out" ? "info" : l.tone]}>
                  {l.time && <span className="text-term-muted">{l.time} </span>}
                  {l.text}
                </div>
              ))}
            </pre>
          ) : (
            <p className="m-0 text-term-muted">{empty}</p>
          )}
        </div>
      </div>
    </section>
  );
}
