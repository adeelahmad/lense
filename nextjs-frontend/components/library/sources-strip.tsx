"use client";

import {
  Box,
  CalendarDays,
  Cloud,
  Folder,
  Globe,
  HardDrive,
  ListOrdered,
  Mail,
  Network,
  Server,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import type { Source, Watch } from "@/app/openapi-client/types.gen";
import { sourceTypeLabel } from "@/components/library/source-labels";
import { Tooltip } from "@/components/ui/tooltip";
import { count, relative } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICON: Record<string, LucideIcon> = {
  s3: Cloud,
  dropbox: Box,
  drive: HardDrive,
  onedrive: Cloud,
  sftp: Server,
  smb: Network,
  webdav: Globe,
  local: Folder,
  imap: Mail,
  ical: CalendarDays,
};

type ChipState = { meta: string; tone: "muted" | "red"; tip: string };

export function watchState(w: Watch, src: Source | undefined, now = Date.now()): ChipState {
  if (src?.health && src.health.ok === false)
    return {
      meta: "✕ can’t be reached",
      tone: "red",
      tip: src.health.error || "The last connection test failed.",
    };
  if (w.last_error) return { meta: "✕ last scan failed", tone: "red", tip: w.last_error };
  if (w.enabled === false)
    return {
      meta: "paused",
      tone: "muted",
      tip: "Not watching: turned off in Sources.",
    };
  const every = w.poll_minutes ? `every ${w.poll_minutes} min` : "watching";
  const last = w.last_scan_at ? relative(w.last_scan_at, now) : null;
  return {
    meta: last ? `${every} · ${last}` : every,
    tone: "muted",
    tip: last ? `Watching · last looked ${last}` : "Watching",
  };
}

/** "Is anything arriving?": every watched folder feeding these namespaces, and the job queue. Links to Sources. */
export function SourcesStrip({
  watches,
  sources,
  running,
  queued,
}: {
  watches: Watch[];
  sources: Source[];
  running: number;
  queued: number;
}) {
  if (!watches.length && !running && !queued) return null;
  const byId = new Map(sources.map((s) => [s.id, s]));
  return (
    <div className="flex items-center gap-2" aria-label="Watched sources and queue" role="group">
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto [scrollbar-width:none]">
        {watches.map((w) => {
          const src = byId.get(w.source);
          const st = watchState(w, src);
          const Icon = ICON[src?.type ?? ""] ?? HardDrive;
          const base = w.source_name || src?.name || sourceTypeLabel(src?.type);
          const name = w.path && !base.includes(w.path) ? `${base} · ${w.path}` : base;
          return (
            <Tooltip key={w.id} content={st.tip}>
              <Link
                href="/sources"
                className={cn(
                  "flex h-[30px] shrink-0 items-center gap-[7px] whitespace-nowrap rounded-pill border pl-[9px] pr-[11px] text-[12.5px] font-medium text-fg-strong hover:bg-surface",
                  st.tone === "red"
                    ? "border-red-border bg-red-surface hover:bg-red-surface"
                    : "border-border bg-background",
                )}
              >
                <Icon className="size-3.5" aria-hidden />
                <b className="font-semibold">{name}</b>
                <span className={st.tone === "red" ? "text-red-dark" : "text-fg-muted"}>{st.meta}</span>
              </Link>
            </Tooltip>
          );
        })}
      </div>
      {(running > 0 || queued > 0 || watches.length > 0) && (
        <Link
          href="/activity"
          className="tabular flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12.5px] font-medium text-fg-secondary hover:text-fg"
        >
          <ListOrdered className="size-3.5" aria-hidden />
          Queue: {count(running)} running · {count(queued)} waiting
        </Link>
      )}
    </div>
  );
}
