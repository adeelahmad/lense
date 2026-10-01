"use client";

import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useEffect, useRef } from "react";

import { useRec } from "@/components/recording/context";

/** Find in the text: the query, which match of how many, and the way to the next or previous one. "/" opens it in a
 * transcript (matches are highlighted in the text and marked ▲ on the waveform) and in a document's text. */
export function FindBar({ what = "the transcript" }: { what?: string }) {
  const { find } = useRec();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [find.open]);
  const n = find.hits.length;
  const go = (d: number) => n && find.setIndex((find.index + d + n) % n);
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2" role="search">
      <span className="relative min-w-0 flex-1">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-muted"
        />
        <input
          ref={input}
          type="search"
          value={find.query}
          placeholder={`Find in ${what}`}
          aria-label={`Find in ${what}`}
          onChange={(e) => {
            find.setQuery(e.target.value);
            find.setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              go(e.shiftKey ? -1 : 1);
            } else if (e.key === "Escape") {
              e.preventDefault();
              find.setOpen(false);
              find.setQuery("");
            }
          }}
          className="h-8 w-full rounded-sm border border-border bg-background pl-8 pr-2 text-[13px] text-fg outline-none placeholder:text-fg-muted focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)]"
        />
      </span>
      <span role="status" className="tabular min-w-[64px] whitespace-nowrap text-[12px] text-fg-muted">
        {find.query.trim().length < 2 ? "" : n ? `${find.index + 1} of ${n}` : "No matches"}
      </span>
      <button
        type="button"
        aria-label="Previous match (Shift+Enter)"
        disabled={!n}
        onClick={() => go(-1)}
        className="grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral disabled:opacity-40"
      >
        <ChevronUp className="size-4" />
      </button>
      <button
        type="button"
        aria-label="Next match (Enter)"
        disabled={!n}
        onClick={() => go(1)}
        className="grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral disabled:opacity-40"
      >
        <ChevronDown className="size-4" />
      </button>
      <button
        type="button"
        aria-label="Close find (Esc)"
        onClick={() => {
          find.setOpen(false);
          find.setQuery("");
        }}
        className="grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
