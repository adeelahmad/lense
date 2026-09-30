"use client";

import { ChevronDown } from "lucide-react";

import { usePlayerApi, usePlayerState } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { chapterAt } from "@/components/recording/model";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The chapter rail (200px): made by Analyze; the current chapter is tinted; click to jump. */
export function ChaptersRail({ className }: { className?: string }) {
  const { model, state } = useRec();
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const cur = chapterAt(model.chapters, time);
  const pending = !model.chapters.length && (state.phase === "processing" || state.phase === "analyzing");
  return (
    <aside aria-label="Chapters" className={cn("min-h-0 flex-col gap-0.5 overflow-y-auto border-r border-border bg-surface px-2.5 py-3.5", className)}>
      <div className="flex items-baseline justify-between px-2 pb-2">
        <h2 className="label-caps">Chapters</h2>
        <span className="text-[11px] font-medium text-fg-muted">{model.chapters.length || "—"}</span>
      </div>
      {!model.chapters.length && (
        <>
          <p className="px-2 py-1 text-[13px] leading-[1.45] text-fg-muted">
            {pending ? "Chapters are created by the Analyze step." : "No chapters: Analyze found no topic changes, or hasn't run on this recording."}
          </p>
          {pending && (
            <div className="flex flex-col gap-2 px-2 py-1" aria-hidden>
              <Skeleton className="h-2.5 w-4/5 !animate-none" />
              <Skeleton className="h-2.5 w-3/5 !animate-none" />
              <Skeleton className="h-2.5 w-[70%] !animate-none" />
            </div>
          )}
        </>
      )}
      <ol className="m-0 flex list-none flex-col gap-0.5 p-0">
        {model.chapters.map((c, i) => (
          <li key={i}>
            <button
              type="button"
              aria-current={i === cur ? "true" : undefined}
              onClick={() => api.seek(c.t0, { manual: true })}
              className={cn("grid w-full grid-cols-[18px_1fr] gap-x-1.5 gap-y-0.5 rounded-sm px-2 py-[7px] text-left hover:bg-surface-neutral", i === cur && "bg-hl hover:bg-hl")}
            >
              <span className="tabular text-[11px] font-semibold leading-[17px] text-fg-muted">{i + 1}</span>
              <span className={cn("text-[13px] leading-[1.3] text-fg [text-wrap:pretty]", i === cur ? "font-bold" : "font-medium")}>{c.title}</span>
              <span />
              <span className="tabular text-[11.5px] leading-none text-fg-muted">{tc(c.t0)}</span>
            </button>
          </li>
        ))}
      </ol>
    </aside>
  );
}

/** The current chapter's label ("5 · Red-teaming findings"); where the rail is hidden it opens a chapter menu. */
export function ChapterNow({ menu, className }: { menu?: boolean; className?: string }) {
  const { model, state } = useRec();
  const api = usePlayerApi();
  const { time } = usePlayerState();
  const cur = chapterAt(model.chapters, time);
  const label =
    state.phase === "processing" ? (model.audio ? "Audio plays while it's processed" : "Processing") : cur >= 0 ? `${cur + 1} · ${model.chapters[cur].title}` : model.chapters.length ? "Before the first chapter" : "";
  if (!menu || !model.chapters.length)
    return <span className={cn("ml-1.5 min-w-0 truncate text-[13px] font-medium leading-none text-fg-secondary", className)}>{label}</span>;
  return (
    <Menu>
      <MenuTrigger asChild>
        <button type="button" className={cn("ml-1 flex min-w-0 items-center gap-1 rounded-sm px-1.5 py-1 text-[13px] font-medium text-fg-secondary hover:bg-surface-neutral", className)}>
          <span className="truncate">{label}</span>
          <ChevronDown className="size-3.5 shrink-0" />
        </button>
      </MenuTrigger>
      <MenuContent align="start" className="max-h-[360px] overflow-y-auto">
        <MenuLabel>Chapters</MenuLabel>
        {model.chapters.map((c, i) => (
          <MenuItem key={i} onSelect={() => api.seek(c.t0, { manual: true })} shortcut={tc(c.t0)}>
            <span className={cn(i === cur && "font-bold")}>
              {i + 1} · {c.title}
            </span>
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}
