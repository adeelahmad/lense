"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export type FindItem = { id: number; name: string; meta?: string };

/** A find box with its list of matches: arrow keys move, Enter picks, Escape leaves. The caller finds the matches
 * for the typed text `q`. */
export function FindBox({
  q,
  onQ,
  items,
  loading,
  empty,
  onPick,
  onCancel,
  placeholder,
  label,
  autoFocus,
  busy,
}: {
  q: string;
  onQ: (q: string) => void;
  items: FindItem[];
  /** The list doesn't show the matches for `q` yet (a search is on its way); Enter waits for them. */
  loading?: boolean;
  /** What shows when nothing matches; nothing shows while it's undefined (still looking). */
  empty?: string;
  onPick: (item: FindItem) => void;
  onCancel?: () => void;
  placeholder?: string;
  label: string;
  autoFocus?: boolean;
  busy?: boolean;
}) {
  const listId = useId();
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, [autoFocus]);
  const first = items[0]?.id;
  useEffect(() => setActive(0), [first]);
  const pick = (it: FindItem | undefined) => {
    if (!it || busy) return;
    onPick(it);
    onQ("");
  };
  // Enter before the list caught up with what was typed picks from the list for that text, not the one still showing
  const [want, setWant] = useState(false);
  useEffect(() => {
    if (!want || loading) return;
    setWant(false);
    pick(items[active]);
  });
  return (
    <div className="flex flex-col gap-1">
      <div className="relative">
        <input
          ref={input}
          role="combobox"
          aria-label={label}
          aria-expanded={items.length > 0}
          aria-controls={listId}
          aria-activedescendant={items[active] ? `${listId}-${items[active].id}` : undefined}
          aria-autocomplete="list"
          value={q}
          placeholder={placeholder}
          disabled={busy}
          onChange={(e) => {
            setActive(0);
            onQ(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(items.length - 1, a + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(0, a - 1));
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (loading) setWant(true);
              else pick(items[active]);
            } else if (e.key === "Escape" && onCancel) {
              e.preventDefault();
              e.stopPropagation();
              onCancel();
            }
          }}
          className="h-9 w-full rounded-sm border border-border bg-background px-3 pr-9 text-[14px] text-fg outline-hidden placeholder:text-fg-muted focus:border-blue focus:shadow-[0_0_0_3px_var(--intent-surface)]"
        />
        {(busy || loading) && (
          <Loader2 aria-hidden className="absolute right-3 top-2.5 size-4 animate-spin text-fg-muted" />
        )}
      </div>
      <ul id={listId} role="listbox" aria-label={label} className="m-0 flex list-none flex-col p-0">
        {items.map((it, i) => (
          <li
            key={it.id}
            id={`${listId}-${it.id}`}
            role="option"
            aria-selected={i === active}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(ev) => ev.preventDefault()}
            onClick={() => pick(it)}
            className={cn(
              "flex h-8 cursor-pointer items-center gap-2 rounded-sm px-2 text-[13px]",
              i === active ? "bg-hl" : "hover:bg-surface-neutral",
            )}
          >
            <span className="min-w-0 flex-1 truncate font-semibold text-fg">{it.name}</span>
            {it.meta && <span className="shrink-0 text-[12px] text-fg-muted">{it.meta}</span>}
          </li>
        ))}
        {empty !== undefined && !items.length && <li className="px-2 py-1.5 text-[12.5px] text-fg-muted">{empty}</li>}
      </ul>
    </div>
  );
}
