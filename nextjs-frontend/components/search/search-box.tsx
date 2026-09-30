"use client";

import { Search, X } from "lucide-react";
import { forwardRef, type KeyboardEvent } from "react";

import { SyntaxHelp } from "@/components/search/syntax-help";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { cn } from "@/lib/utils";

export type Chip = { key: string; label: string; onRemove: () => void };

/** A filter chip: the same filter as its facet. */
export function FilterChip({ chip, className }: { chip: Chip; className?: string }) {
  return (
    <span className={cn("inline-flex h-[26px] shrink-0 items-center gap-1 rounded-pill border border-blue-border bg-blue-surface pl-2 pr-1 text-[12px] font-semibold text-fg-accent", className)}>
      {chip.label}
      <button type="button" onClick={chip.onRemove} aria-label={`Remove filter ${chip.label}`} className="grid size-5 place-items-center rounded-full hover:bg-blue-border/40">
        <X className="size-3" aria-hidden />
      </button>
    </span>
  );
}

/**
 * The search box: words in a monospaced field, filter chips inside it, and the syntax help behind "?".
 * Enter searches; ↓ moves into the results.
 */
export const SearchBox = forwardRef<
  HTMLInputElement,
  {
    value: string;
    onChange: (v: string) => void;
    onSubmit: () => void;
    onClear?: () => void;
    onArrowDown?: () => void;
    chips: Chip[];
    helpOpen: boolean;
    onHelpOpenChange: (open: boolean) => void;
    onPickExample?: (example: string) => void;
    compact?: boolean;
    className?: string;
  }
>(function SearchBox({ value, onChange, onSubmit, onClear, onArrowDown, chips, helpOpen, onHelpOpenChange, onPickExample, compact, className }, ref) {
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onSubmit();
    } else if (e.key === "ArrowDown" && onArrowDown) {
      e.preventDefault();
      onArrowDown();
    }
  };
  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className={cn(
        "flex h-11 min-w-0 flex-1 items-center gap-2 rounded-[10px] border border-border bg-background pl-3.5 pr-2 transition-[border-color,box-shadow] duration-fast focus-within:border-blue focus-within:shadow-[0_0_0_3px_var(--intent-surface)]",
        className,
      )}
    >
      <Search className="size-[17px] shrink-0 text-fg-muted" aria-hidden />
      <input
        ref={ref}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKey}
        aria-label="Search transcripts"
        placeholder={compact ? "Search what was said" : 'Search what was said — words, "phrases", OR, speaker:…'}
        autoComplete="off"
        spellCheck={false}
        className="h-full min-w-0 flex-1 bg-transparent font-mono text-[15px] text-fg outline-none placeholder:font-sans placeholder:text-[14px] placeholder:text-fg-muted [&::-webkit-search-cancel-button]:hidden"
      />
      {!compact && chips.map((c) => <FilterChip key={c.key} chip={c} />)}
      {compact && value && onClear && (
        <button type="button" onClick={onClear} aria-label="Clear search" className="grid size-8 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral">
          <X className="size-[17px]" />
        </button>
      )}
      {!compact && (
        <Popover open={helpOpen} onOpenChange={onHelpOpenChange}>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label="Search syntax help"
              className={cn("grid size-[30px] shrink-0 place-items-center rounded-full text-[13px] font-bold text-fg", helpOpen ? "bg-blue-surface text-fg-accent" : "bg-surface-neutral hover:bg-border/60")}
            >
              ?
            </button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[400px] max-w-[calc(100vw-32px)] rounded-[14px] p-4 shadow-3">
            <SyntaxHelp
              onPick={(ex) => {
                onPickExample?.(ex);
                onHelpOpenChange(false);
              }}
            />
          </PopoverContent>
        </Popover>
      )}
    </form>
  );
});
